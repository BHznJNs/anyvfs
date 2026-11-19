package bridge

import (
	"flag"
	"fmt"
	"net"
	"os"
	"time"
)

type programInput struct {
	endpoint        string
	mountpoint      string
	maxMessageBytes int
	startupTimeout  time.Duration
	token           string
}

func parseInput() (programInput, error) {
	var input programInput
	flag.StringVar(&input.endpoint, "endpoint", "", "Python controller loopback address")
	flag.StringVar(&input.mountpoint, "mountpoint", "", "mount path or unused Windows drive")
	flag.IntVar(&input.maxMessageBytes, "max-message-bytes", 16<<20, "gRPC message size")
	flag.DurationVar(&input.startupTimeout, "startup-timeout", 25*time.Second, "bootstrap deadline")
	flag.Parse()

	input.token = os.Getenv("ANYVFS_TOKEN")
	os.Unsetenv("ANYVFS_TOKEN")
	hostIP, _, err := net.SplitHostPort(input.endpoint)
	if err != nil || hostIP != "127.0.0.1" || input.token == "" || input.mountpoint == "" || input.startupTimeout <= 0 {
		return input, fmt.Errorf("loopback endpoint, token, mountpoint and positive startup timeout required")
	}
	return input, nil
}
