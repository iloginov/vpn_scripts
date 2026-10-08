package main

import (
	"context"
	"flag"
	"log"
	"os"
	"os/signal"
	"syscall"

	"vpn_scripts/failover/internal/agent"
	"vpn_scripts/failover/internal/config"
	"vpn_scripts/failover/internal/sys"
)

func main() {
	configPath := flag.String("config", "/etc/awg-failover/config.yaml", "путь к конфигу")
	dryRun := flag.Bool("dry-run", false, "решать и писать в журнал, маршрут не менять")
	verbose := flag.Bool("v", false, "писать каждую пробу")
	flag.Parse()

	cfg, err := config.Load(*configPath)
	if err != nil {
		log.Fatal(err)
	}
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	if err := agent.Run(ctx, cfg, sys.OS{}, log.Default(), agent.Options{DryRun: *dryRun, Verbose: *verbose}); err != nil {
		log.Fatal(err)
	}
	os.Exit(0)
}
