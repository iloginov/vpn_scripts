package config

import (
	"fmt"
	"os"
	"time"

	"gopkg.in/yaml.v3"
)

type Config struct {
	Interval         time.Duration
	RouteTable       string
	ProbeTarget      string
	ProbeTimeout     time.Duration
	HandshakeTimeout time.Duration
	FailCount        int
	ImproveMargin    time.Duration
	ImproveRatio     float64
	ImproveHold      time.Duration
	EMAAlpha         float64
	Uplinks          []Uplink
}

type Uplink struct {
	Iface string
}

type file struct {
	Interval         string   `yaml:"interval"`
	RouteTable       string   `yaml:"route_table"`
	ProbeTarget      string   `yaml:"probe_target"`
	ProbeTimeout     string   `yaml:"probe_timeout"`
	HandshakeTimeout string   `yaml:"handshake_timeout"`
	FailCount        int      `yaml:"fail_count"`
	ImproveMargin    string   `yaml:"improve_margin"`
	ImproveRatio     float64  `yaml:"improve_ratio"`
	ImproveHold      string   `yaml:"improve_hold"`
	EMAAlpha         float64  `yaml:"ema_alpha"`
	Uplinks          []Uplink `yaml:"uplinks"`
}

func Load(path string) (Config, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return Config{}, err
	}
	var f file
	if err := yaml.Unmarshal(raw, &f); err != nil {
		return Config{}, err
	}
	cfg := Config{
		RouteTable:   f.RouteTable,
		ProbeTarget:  f.ProbeTarget,
		FailCount:    f.FailCount,
		ImproveRatio: f.ImproveRatio,
		EMAAlpha:     f.EMAAlpha,
		Uplinks:      f.Uplinks,
	}
	var errParse error
	cfg.Interval, errParse = parseDur("interval", f.Interval, errParse)
	cfg.ProbeTimeout, errParse = parseDur("probe_timeout", f.ProbeTimeout, errParse)
	cfg.HandshakeTimeout, errParse = parseDur("handshake_timeout", f.HandshakeTimeout, errParse)
	cfg.ImproveMargin, errParse = parseDur("improve_margin", f.ImproveMargin, errParse)
	cfg.ImproveHold, errParse = parseDur("improve_hold", f.ImproveHold, errParse)
	if errParse != nil {
		return Config{}, errParse
	}
	if err := cfg.Validate(); err != nil {
		return Config{}, err
	}
	return cfg, nil
}

func parseDur(name, value string, prev error) (time.Duration, error) {
	if prev != nil {
		return 0, prev
	}
	d, err := time.ParseDuration(value)
	if err != nil {
		return 0, fmt.Errorf("%s: %w", name, err)
	}
	return d, nil
}

func (c Config) Validate() error {
	if c.Interval <= 0 {
		return fmt.Errorf("interval должен быть больше нуля")
	}
	if c.RouteTable == "" {
		return fmt.Errorf("route_table пуст")
	}
	if c.ProbeTarget == "" {
		return fmt.Errorf("probe_target пуст")
	}
	if c.ProbeTimeout <= 0 {
		return fmt.Errorf("probe_timeout должен быть больше нуля")
	}
	if c.HandshakeTimeout <= 0 {
		return fmt.Errorf("handshake_timeout должен быть больше нуля")
	}
	if c.FailCount < 1 {
		return fmt.Errorf("fail_count должен быть не меньше 1")
	}
	if c.ImproveMargin < 0 || c.ImproveHold < 0 {
		return fmt.Errorf("improve_margin и improve_hold не могут быть отрицательными")
	}
	if c.ImproveRatio < 0 {
		return fmt.Errorf("improve_ratio не может быть отрицательным")
	}
	if c.EMAAlpha <= 0 || c.EMAAlpha > 1 {
		return fmt.Errorf("ema_alpha должен быть в (0, 1]")
	}
	if len(c.Uplinks) == 0 {
		return fmt.Errorf("uplinks пуст")
	}
	seen := map[string]bool{}
	for _, u := range c.Uplinks {
		if u.Iface == "" {
			return fmt.Errorf("uplink с пустым iface")
		}
		if seen[u.Iface] {
			return fmt.Errorf("uplink %s повторяется", u.Iface)
		}
		seen[u.Iface] = true
	}
	return nil
}

func (c Config) Ifaces() []string {
	out := make([]string, len(c.Uplinks))
	for i, u := range c.Uplinks {
		out[i] = u.Iface
	}
	return out
}
