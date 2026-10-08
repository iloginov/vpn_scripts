package decide

import (
	"testing"
	"time"
)

func params() Params {
	return Params{
		Ifaces:        []string{"awg1", "awg2"},
		FailCount:     3,
		ImproveMargin: 30 * time.Millisecond,
		ImproveRatio:  0.2,
		ImproveHold:   3 * time.Minute,
		EMAAlpha:      0.5,
	}
}

func ok(iface string, rtt time.Duration) Sample {
	return Sample{Iface: iface, HandshakeOK: true, ProbeOK: true, RTT: rtt}
}

func deadHS(iface string) Sample {
	return Sample{Iface: iface, HandshakeOK: false, ProbeOK: false}
}

func TestDownSwitchesToLowerRTT(t *testing.T) {
	p := params()
	now := time.Unix(0, 0)
	state, _ := Step(now, p, State{}, []Sample{ok("awg1", 40*time.Millisecond), ok("awg2", 80*time.Millisecond)})
	if state.Active != "awg1" {
		t.Fatalf("active=%s", state.Active)
	}

	state, action := Step(now, p, state, []Sample{deadHS("awg1"), ok("awg2", 80*time.Millisecond)})
	if !action.Switch || action.Reason != ReasonDown || action.To != "awg2" {
		t.Fatalf("action=%+v", action)
	}
	if state.Active != "awg2" {
		t.Fatalf("active=%s", state.Active)
	}
}

func TestAllDeadKeepsActive(t *testing.T) {
	p := params()
	now := time.Unix(0, 0)
	state := State{Active: "awg1"}
	state, action := Step(now, p, state, []Sample{deadHS("awg1"), deadHS("awg2")})
	if action.Switch {
		t.Fatalf("action=%+v", action)
	}
	if state.Active != "awg1" {
		t.Fatalf("active=%s", state.Active)
	}
}

func TestProbeBlipsDoNotSwitch(t *testing.T) {
	p := params()
	now := time.Unix(0, 0)
	state, _ := Step(now, p, State{Active: "awg1"}, []Sample{ok("awg1", 40*time.Millisecond), ok("awg2", 50*time.Millisecond)})
	var action Action
	for i := 0; i < 2; i++ {
		state, action = Step(now, p, state, []Sample{
			{Iface: "awg1", HandshakeOK: true, ProbeOK: false},
			ok("awg2", 50*time.Millisecond),
		})
		if action.Switch {
			t.Fatalf("switch on blip %d: %+v", i, action)
		}
	}
	if state.Links["awg1"].FailStreak != 2 {
		t.Fatalf("streak=%d", state.Links["awg1"].FailStreak)
	}
}

func TestProbeFailCountSwitches(t *testing.T) {
	p := params()
	now := time.Unix(0, 0)
	state, _ := Step(now, p, State{Active: "awg1"}, []Sample{ok("awg1", 40*time.Millisecond), ok("awg2", 50*time.Millisecond)})
	var action Action
	for i := 0; i < 3; i++ {
		state, action = Step(now, p, state, []Sample{
			{Iface: "awg1", HandshakeOK: true, ProbeOK: false},
			ok("awg2", 50*time.Millisecond),
		})
	}
	if !action.Switch || action.Reason != ReasonDown || action.To != "awg2" {
		t.Fatalf("action=%+v", action)
	}
}

func TestSmallGapDoesNotSwitch(t *testing.T) {
	p := params()
	now := time.Unix(0, 0)
	state := State{Active: "awg1"}
	state, action := Step(now, p, state, []Sample{ok("awg1", 50*time.Millisecond), ok("awg2", 40*time.Millisecond)})
	if action.Switch || state.Pending != "" {
		t.Fatalf("action=%+v pending=%s", action, state.Pending)
	}
}

func TestLatencyNeedsHold(t *testing.T) {
	p := params()
	start := time.Unix(1_700_000_000, 0)
	state := State{Active: "awg1"}
	state, action := Step(start, p, state, []Sample{ok("awg1", 100*time.Millisecond), ok("awg2", 40*time.Millisecond)})
	if action.Switch || state.Pending != "awg2" {
		t.Fatalf("action=%+v pending=%s", action, state.Pending)
	}
	state, action = Step(start.Add(2*time.Minute), p, state, []Sample{ok("awg1", 100*time.Millisecond), ok("awg2", 40*time.Millisecond)})
	if action.Switch {
		t.Fatalf("switched too early: %+v", action)
	}
	state, action = Step(start.Add(3*time.Minute), p, state, []Sample{ok("awg1", 100*time.Millisecond), ok("awg2", 40*time.Millisecond)})
	if !action.Switch || action.Reason != ReasonLatency || action.To != "awg2" {
		t.Fatalf("action=%+v", action)
	}
}

func TestLeaderChangeResetsHold(t *testing.T) {
	p := params()
	p.EMAAlpha = 1
	p.Ifaces = []string{"awg1", "awg2", "awg3"}
	start := time.Unix(1_700_000_000, 0)
	state := State{Active: "awg1"}
	state, _ = Step(start, p, state, []Sample{ok("awg1", 100*time.Millisecond), ok("awg2", 40*time.Millisecond), ok("awg3", 90*time.Millisecond)})
	if state.Pending != "awg2" {
		t.Fatalf("pending=%s", state.Pending)
	}
	state, action := Step(start.Add(2*time.Minute), p, state, []Sample{ok("awg1", 100*time.Millisecond), ok("awg2", 80*time.Millisecond), ok("awg3", 40*time.Millisecond)})
	if action.Switch {
		t.Fatalf("action=%+v", action)
	}
	if state.Pending != "awg3" || !state.PendingSince.Equal(start.Add(2*time.Minute)) {
		t.Fatalf("pending=%s since=%s", state.Pending, state.PendingSince)
	}
}

func TestRatioRaisesBarOnSlowLink(t *testing.T) {
	p := params()
	now := time.Unix(0, 0)
	// 200 мс и 165 мс: разрыв 35 мс больше абсолютных 30 мс, но меньше 20 %.
	state, action := Step(now, p, State{Active: "awg1"}, []Sample{ok("awg1", 200*time.Millisecond), ok("awg2", 165*time.Millisecond)})
	if action.Switch || state.Pending != "" {
		t.Fatalf("action=%+v pending=%s", action, state.Pending)
	}
}

func TestEmptyActivePicksLowest(t *testing.T) {
	p := params()
	_, action := Step(time.Unix(0, 0), p, State{}, []Sample{ok("awg1", 80*time.Millisecond), ok("awg2", 40*time.Millisecond)})
	if !action.Switch || action.To != "awg2" || action.Reason != ReasonDown {
		t.Fatalf("action=%+v", action)
	}
}

func TestTieKeepsConfigOrder(t *testing.T) {
	p := params()
	_, action := Step(time.Unix(0, 0), p, State{}, []Sample{ok("awg1", 40*time.Millisecond), ok("awg2", 40*time.Millisecond)})
	if action.To != "awg1" {
		t.Fatalf("to=%s", action.To)
	}
}
