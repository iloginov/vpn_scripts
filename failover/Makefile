.PHONY: test build

test:
	go test ./...

build:
	CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o bin/awg-failover ./cmd/awg-failover
