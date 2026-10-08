package sys

import (
	"testing"
	"time"
)

func TestParseHandshakes(t *testing.T) {
	out := "abc=\t0\n" + "def=\t1700000000\n"
	got := ParseHandshakes(out)
	if !got.Equal(time.Unix(1700000000, 0)) {
		t.Fatalf("got %s", got)
	}
	if !ParseHandshakes("peer\t0\n").IsZero() {
		t.Fatal("zero handshake must be empty")
	}
}

func TestParseAddr(t *testing.T) {
	out := "8: awg1    inet 10.10.4.2/32 scope global awg1\n"
	if ParseAddr(out) != "10.10.4.2" {
		t.Fatalf("addr=%q", ParseAddr(out))
	}
}

func TestParseDefaultDev(t *testing.T) {
	out := "default dev awg1 scope link\n"
	if ParseDefaultDev(out) != "awg1" {
		t.Fatalf("dev=%q", ParseDefaultDev(out))
	}
	if ParseDefaultDev("") != "" {
		t.Fatal("empty table")
	}
}

func TestParsePing(t *testing.T) {
	out := "64 bytes from 1.1.1.1: icmp_seq=1 ttl=57 time=12.5 ms\n"
	rtt, ok := ParsePing(out)
	if !ok || rtt != 12500*time.Microsecond {
		t.Fatalf("rtt=%s ok=%v", rtt, ok)
	}
}
