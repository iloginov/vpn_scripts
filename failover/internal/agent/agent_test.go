package agent

import (
	"context"
	"fmt"
	"strings"
	"sync"
	"testing"
	"time"

	"vpn_scripts/failover/internal/config"
)

type fakeSys struct {
	dev   string
	repl  []string
	alive map[string]bool
}

func (f *fakeSys) LatestHandshake(context.Context, string) (time.Time, error) {
	return time.Now(), nil
}

func (f *fakeSys) IPv4(_ context.Context, iface string) (string, error) {
	return "10.0.0." + iface[len(iface)-1:], nil
}

func (f *fakeSys) Ping(_ context.Context, src, _ string, _ time.Duration) (time.Duration, error) {
	if strings.HasSuffix(src, "1") && !f.alive["awg1"] {
		return 0, errPing
	}
	if strings.HasSuffix(src, "1") {
		return 80 * time.Millisecond, nil
	}
	return 40 * time.Millisecond, nil
}

var errPing = errString("ping failed")

type errString string

func (e errString) Error() string { return string(e) }

func (f *fakeSys) DefaultDev(context.Context, string) (string, error) { return f.dev, nil }

func (f *fakeSys) ReplaceDefault(_ context.Context, _, dev string) error {
	f.repl = append(f.repl, dev)
	f.dev = dev
	return nil
}

type bufLog struct {
	mu   sync.Mutex
	line []string
}

func (l *bufLog) Printf(format string, args ...any) {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.line = append(l.line, fmt.Sprintf(format, args...))
}

func TestRunSwitchesWhenActiveDies(t *testing.T) {
	sys := &fakeSys{dev: "awg1", alive: map[string]bool{"awg1": false}}
	log := &bufLog{}
	cfg := config.Config{
		Interval:         20 * time.Millisecond,
		RouteTable:       "clientroute",
		ProbeTarget:      "1.1.1.1",
		ProbeTimeout:     time.Second,
		HandshakeTimeout: 90 * time.Second,
		FailCount:        1,
		ImproveMargin:    30 * time.Millisecond,
		ImproveRatio:     0.2,
		ImproveHold:      time.Minute,
		EMAAlpha:         1,
		Uplinks:          []config.Uplink{{Iface: "awg1"}, {Iface: "awg2"}},
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() {
		_ = Run(ctx, cfg, sys, log, Options{})
		close(done)
	}()
	deadline := time.Now().Add(time.Second)
	for time.Now().Before(deadline) {
		if len(sys.repl) > 0 {
			break
		}
		time.Sleep(5 * time.Millisecond)
	}
	cancel()
	<-done
	if len(sys.repl) != 1 || sys.repl[0] != "awg2" {
		t.Fatalf("repl=%v", sys.repl)
	}
	if !strings.Contains(strings.Join(log.line, "\n"), "awg2") {
		t.Fatalf("log=%v", log.line)
	}
}

func TestDryRunDoesNotReplace(t *testing.T) {
	sys := &fakeSys{dev: "", alive: map[string]bool{}}
	cfg := config.Config{
		Interval:         time.Hour,
		RouteTable:       "clientroute",
		ProbeTarget:      "1.1.1.1",
		ProbeTimeout:     time.Second,
		HandshakeTimeout: 90 * time.Second,
		FailCount:        1,
		ImproveMargin:    30 * time.Millisecond,
		ImproveHold:      time.Minute,
		EMAAlpha:         1,
		Uplinks:          []config.Uplink{{Iface: "awg2"}},
	}
	ctx, cancel := context.WithCancel(context.Background())
	go func() {
		time.Sleep(30 * time.Millisecond)
		cancel()
	}()
	if err := Run(ctx, cfg, sys, &bufLog{}, Options{DryRun: true}); err != nil {
		t.Fatal(err)
	}
	if len(sys.repl) != 0 {
		t.Fatalf("repl=%v", sys.repl)
	}
}
