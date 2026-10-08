package agent

import (
	"context"
	"fmt"
	"time"

	"vpn_scripts/failover/internal/config"
	"vpn_scripts/failover/internal/decide"
)

type System interface {
	LatestHandshake(ctx context.Context, iface string) (time.Time, error)
	IPv4(ctx context.Context, iface string) (string, error)
	Ping(ctx context.Context, src, dst string, timeout time.Duration) (time.Duration, error)
	DefaultDev(ctx context.Context, table string) (string, error)
	ReplaceDefault(ctx context.Context, table, dev string) error
}

type Logger interface {
	Printf(format string, args ...any)
}

type Options struct {
	DryRun  bool
	Verbose bool
}

func Run(ctx context.Context, cfg config.Config, sys System, log Logger, opt Options) error {
	known := map[string]bool{}
	for _, iface := range cfg.Ifaces() {
		known[iface] = true
	}
	current, err := sys.DefaultDev(ctx, cfg.RouteTable)
	if err != nil {
		return fmt.Errorf("таблица %s: %w", cfg.RouteTable, err)
	}
	state := decide.State{}
	if known[current] {
		state.Active = current
	}
	if state.Active == "" {
		log.Printf("active=none table=%s", cfg.RouteTable)
	} else {
		log.Printf("active=%s table=%s", state.Active, cfg.RouteTable)
	}

	p := decide.Params{
		Ifaces:        cfg.Ifaces(),
		FailCount:     cfg.FailCount,
		ImproveMargin: cfg.ImproveMargin,
		ImproveRatio:  cfg.ImproveRatio,
		ImproveHold:   cfg.ImproveHold,
		EMAAlpha:      cfg.EMAAlpha,
	}
	ticker := time.NewTicker(cfg.Interval)
	defer ticker.Stop()
	for {
		if err := ctx.Err(); err != nil {
			return nil
		}
		samples := observe(ctx, cfg, sys, log, opt.Verbose)
		prev := state.Active
		var action decide.Action
		state, action = decide.Step(time.Now(), p, state, samples)
		if action.Switch {
			if opt.DryRun {
				logSwitch(log, action, true)
			} else if err := sys.ReplaceDefault(ctx, cfg.RouteTable, action.To); err != nil {
				log.Printf("route replace dev=%s: %s", action.To, err)
				state.Active = prev
				state.Pending = ""
				state.PendingSince = time.Time{}
			} else {
				logSwitch(log, action, false)
			}
		}
		select {
		case <-ctx.Done():
			return nil
		case <-ticker.C:
		}
	}
}

func observe(ctx context.Context, cfg config.Config, sys System, log Logger, verbose bool) []decide.Sample {
	now := time.Now()
	out := make([]decide.Sample, 0, len(cfg.Uplinks))
	for _, u := range cfg.Uplinks {
		sample := decide.Sample{Iface: u.Iface}
		latest, err := sys.LatestHandshake(ctx, u.Iface)
		if err == nil {
			age := now.Sub(latest)
			sample.HandshakeOK = !latest.IsZero() && age <= cfg.HandshakeTimeout && age > -time.Minute
		}
		addr, err := sys.IPv4(ctx, u.Iface)
		if err == nil {
			rtt, pingErr := sys.Ping(ctx, addr, cfg.ProbeTarget, cfg.ProbeTimeout)
			if pingErr == nil && rtt > 0 {
				sample.ProbeOK = true
				sample.RTT = rtt
			}
		}
		if verbose {
			log.Printf("sample iface=%s handshake_ok=%t probe_ok=%t rtt_ms=%d", u.Iface, sample.HandshakeOK, sample.ProbeOK, sample.RTT.Milliseconds())
		}
		out = append(out, sample)
	}
	return out
}

func logSwitch(log Logger, action decide.Action, dry bool) {
	log.Printf("switch from=%s to=%s reason=%s from_rtt_ms=%d to_rtt_ms=%d dry_run=%t",
		action.From, action.To, action.Reason, action.FromRTT.Milliseconds(), action.ToRTT.Milliseconds(), dry)
}
