package sys

import (
	"bytes"
	"context"
	"fmt"
	"os/exec"
	"regexp"
	"strconv"
	"strings"
	"time"
)

type OS struct{}

func (OS) LatestHandshake(ctx context.Context, iface string) (time.Time, error) {
	out, err := run(ctx, "awg", "show", iface, "latest-handshakes")
	if err != nil {
		return time.Time{}, err
	}
	return ParseHandshakes(out), nil
}

func (OS) IPv4(ctx context.Context, iface string) (string, error) {
	out, err := run(ctx, "ip", "-4", "-o", "addr", "show", "dev", iface)
	if err != nil {
		return "", err
	}
	addr := ParseAddr(out)
	if addr == "" {
		return "", fmt.Errorf("у %s нет ipv4", iface)
	}
	return addr, nil
}

func (OS) Ping(ctx context.Context, src, dst string, timeout time.Duration) (time.Duration, error) {
	sec := int((timeout + time.Second - 1) / time.Second)
	if sec < 1 {
		sec = 1
	}
	pctx, cancel := context.WithTimeout(ctx, timeout+time.Second)
	defer cancel()
	out, err := run(pctx, "ping", "-n", "-I", src, "-c", "1", "-W", strconv.Itoa(sec), dst)
	if err != nil {
		return 0, err
	}
	rtt, ok := ParsePing(out)
	if !ok {
		return 0, fmt.Errorf("ping %s не содержит rtt", dst)
	}
	return rtt, nil
}

func (OS) DefaultDev(ctx context.Context, table string) (string, error) {
	out, err := run(ctx, "ip", "-4", "route", "show", "table", table)
	if err != nil {
		return "", err
	}
	return ParseDefaultDev(out), nil
}

func (OS) ReplaceDefault(ctx context.Context, table, dev string) error {
	_, err := run(ctx, "ip", "route", "replace", "default", "dev", dev, "table", table)
	return err
}

func run(ctx context.Context, name string, args ...string) (string, error) {
	cmd := exec.CommandContext(ctx, name, args...)
	cmd.Env = append(cmd.Environ(), "LC_ALL=C")
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		msg := strings.TrimSpace(stderr.String())
		if msg == "" {
			msg = strings.TrimSpace(stdout.String())
		}
		if msg == "" {
			return "", fmt.Errorf("%s: %w", name, err)
		}
		return "", fmt.Errorf("%s: %s", name, msg)
	}
	return stdout.String(), nil
}

func ParseHandshakes(out string) time.Time {
	var latest time.Time
	for _, line := range strings.Split(out, "\n") {
		fields := strings.Fields(line)
		if len(fields) < 2 {
			continue
		}
		sec, err := strconv.ParseInt(fields[len(fields)-1], 10, 64)
		if err != nil || sec <= 0 {
			continue
		}
		t := time.Unix(sec, 0)
		if t.After(latest) {
			latest = t
		}
	}
	return latest
}

func ParseAddr(out string) string {
	fields := strings.Fields(out)
	for i, f := range fields {
		if f == "inet" && i+1 < len(fields) {
			return strings.Split(fields[i+1], "/")[0]
		}
	}
	return ""
}

func ParseDefaultDev(out string) string {
	for _, line := range strings.Split(out, "\n") {
		fields := strings.Fields(line)
		if len(fields) < 3 || fields[0] != "default" {
			continue
		}
		for i := 1; i < len(fields)-1; i++ {
			if fields[i] == "dev" {
				return fields[i+1]
			}
		}
	}
	return ""
}

var pingRTT = regexp.MustCompile(`time[=<]([0-9]+(?:\.[0-9]+)?)\s*ms`)

func ParsePing(out string) (time.Duration, bool) {
	m := pingRTT.FindStringSubmatch(out)
	if m == nil {
		return 0, false
	}
	ms, err := strconv.ParseFloat(m[1], 64)
	if err != nil || ms <= 0 {
		return 0, false
	}
	return time.Duration(ms * float64(time.Millisecond)), true
}
