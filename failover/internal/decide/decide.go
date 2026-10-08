// Пакет decide решает, какой uplink держит default в clientroute.
// Правила маршрутизации и пробы он не создаёт: на вход уже готовые наблюдения.
package decide

import "time"

const (
	ReasonDown    = "down"
	ReasonLatency = "latency"
)

type Params struct {
	Ifaces        []string
	FailCount     int
	ImproveMargin time.Duration
	ImproveRatio  float64
	ImproveHold   time.Duration
	EMAAlpha      float64
}

type Sample struct {
	Iface       string
	HandshakeOK bool
	ProbeOK     bool
	RTT         time.Duration
}

type Link struct {
	EMA        time.Duration
	HasEMA     bool
	FailStreak int
}

type State struct {
	Active       string
	Pending      string
	PendingSince time.Time
	Links        map[string]Link
}

type Action struct {
	Switch  bool
	Reason  string
	From    string
	To      string
	FromRTT time.Duration
	ToRTT   time.Duration
}

func Step(now time.Time, p Params, state State, samples []Sample) (State, Action) {
	next := clone(state)
	if next.Links == nil {
		next.Links = map[string]Link{}
	}
	byIface := index(samples)
	for _, iface := range p.Ifaces {
		next.Links[iface] = updateLink(next.Links[iface], byIface[iface], p.EMAAlpha)
	}

	if next.Active != "" && !healthyStay(next.Links[next.Active], byIface[next.Active], p.FailCount) {
		to := bestFresh(p.Ifaces, next.Links, byIface, "")
		next.Pending = ""
		next.PendingSince = time.Time{}
		if to == "" || to == next.Active {
			return next, Action{}
		}
		return switched(next, to), Action{
			Switch:  true,
			Reason:  ReasonDown,
			From:    state.Active,
			To:      to,
			FromRTT: emaOf(next, state.Active),
			ToRTT:   emaOf(next, to),
		}
	}

	if next.Active == "" {
		to := bestFresh(p.Ifaces, next.Links, byIface, "")
		if to == "" {
			return next, Action{}
		}
		return switched(next, to), Action{
			Switch: true,
			Reason: ReasonDown,
			To:     to,
			ToRTT:  emaOf(next, to),
		}
	}

	activeEMA, ok := comparableEMA(next.Links[next.Active])
	if !ok {
		next.Pending = ""
		next.PendingSince = time.Time{}
		return next, Action{}
	}
	to := bestFresh(p.Ifaces, next.Links, byIface, next.Active)
	if to == "" || !beats(activeEMA, next.Links[to].EMA, p) {
		next.Pending = ""
		next.PendingSince = time.Time{}
		return next, Action{}
	}
	if next.Pending != to {
		next.Pending = to
		next.PendingSince = now
		return next, Action{}
	}
	if now.Sub(next.PendingSince) < p.ImproveHold {
		return next, Action{}
	}
	from := next.Active
	return switched(next, to), Action{
		Switch:  true,
		Reason:  ReasonLatency,
		From:    from,
		To:      to,
		FromRTT: activeEMA,
		ToRTT:   next.Links[to].EMA,
	}
}

func switched(s State, to string) State {
	s.Active = to
	s.Pending = ""
	s.PendingSince = time.Time{}
	return s
}

func updateLink(l Link, s Sample, alpha float64) Link {
	if s.ProbeOK && s.RTT > 0 {
		l.FailStreak = 0
		if !l.HasEMA {
			l.EMA = s.RTT
			l.HasEMA = true
		} else {
			l.EMA = time.Duration(alpha*float64(s.RTT) + (1-alpha)*float64(l.EMA))
		}
		return l
	}
	l.FailStreak++
	return l
}

func healthyStay(l Link, s Sample, failCount int) bool {
	return s.HandshakeOK && l.FailStreak < failCount
}

func bestFresh(order []string, links map[string]Link, samples map[string]Sample, skip string) string {
	best := ""
	var bestEMA time.Duration
	for _, iface := range order {
		if iface == skip {
			continue
		}
		s := samples[iface]
		l := links[iface]
		if !s.HandshakeOK || !s.ProbeOK || !l.HasEMA {
			continue
		}
		if best == "" || l.EMA < bestEMA {
			best = iface
			bestEMA = l.EMA
		}
	}
	return best
}

func beats(active, other time.Duration, p Params) bool {
	if other <= 0 || active <= other {
		return false
	}
	return active-other >= requiredGap(active, p)
}

func requiredGap(active time.Duration, p Params) time.Duration {
	gap := p.ImproveMargin
	if p.ImproveRatio > 0 {
		rel := time.Duration(float64(active) * p.ImproveRatio)
		if rel > gap {
			gap = rel
		}
	}
	return gap
}

func comparableEMA(l Link) (time.Duration, bool) {
	if !l.HasEMA || l.EMA <= 0 {
		return 0, false
	}
	return l.EMA, true
}

func emaOf(s State, iface string) time.Duration {
	l, ok := s.Links[iface]
	if !ok || !l.HasEMA {
		return 0
	}
	return l.EMA
}

func index(samples []Sample) map[string]Sample {
	out := make(map[string]Sample, len(samples))
	for _, s := range samples {
		out[s.Iface] = s
	}
	return out
}

func clone(s State) State {
	n := State{Active: s.Active, Pending: s.Pending, PendingSince: s.PendingSince, Links: map[string]Link{}}
	for k, v := range s.Links {
		n.Links[k] = v
	}
	return n
}
