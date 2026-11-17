// Package bridge runs the native filesystem bridge.
package bridge

import (
	"context"
	"fmt"
	"log/slog"
	"os"
	"os/signal"
	"syscall"
	"time"

	"vfspy/native/internal/memfs"
	"vfspy/native/internal/queue"
	pb "vfspy/native/internal/rpc"
)

// Run starts the filesystem bridge and waits for shutdown.
func Run() error {
	input, err := parseInput()
	if err != nil {
		return err
	}

	slog.SetDefault(slog.Default().With("mountpoint", input.mountpoint))
	slog.Info("bridge starting")
	client, err := newControllerClient(input)
	if err != nil {
		return err
	}
	defer client.Close()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	config, entries, err := client.Startup(ctx)
	if err != nil {
		return err
	}
	// The filesystem and host produce notices; the sender is the only consumer.
	notices := queue.New[*pb.Notice]()
	fs, err := memfs.New(config, entries, notices)
	if err != nil {
		return err
	}
	slog.Debug("initial data imported", "entries", len(entries))
	stream, err := client.Session(ctx)
	if err != nil {
		return err
	}
	host := newFuseHost(fs, config.CaseSensitive, notices)
	initialized := make(chan struct{})
	mountDone := make(chan struct{})
	fs.OnInit = func() {
		close(initialized)
		slog.Info("filesystem mounted")
	}
	disconnect := make(chan struct{})
	receiverDone := make(chan struct{})
	go func() {
		defer close(receiverDone)
		defer close(disconnect)
		receiveCommands(ctx, stream, fs)
	}()
	senderDone := make(chan error, 1)
	go func() {
		senderDone <- sendNotices(ctx, stream, notices, cancel)
	}()
	signals := make(chan os.Signal, 1)
	signal.Notify(signals, os.Interrupt, syscall.SIGTERM)
	defer signal.Stop(signals)
	go func() {
		select {
		case <-disconnect:
		case received := <-signals:
			slog.Info("shutdown signal received", "signal", received.String())
		case <-ctx.Done():
		case <-mountDone:
			return
		}
		select {
		case <-initialized:
			if !host.Unmount() {
				slog.Warn("filesystem unmount request failed")
			}
		case <-mountDone:
		}
	}()
	mounted := host.Mount(input.mountpoint)
	close(mountDone)
	select {
	case e := <-senderDone:
		if e != nil && mounted {
			return e
		}
	case <-time.After(10 * time.Second):
		return fmt.Errorf("event stream did not drain")
	}
	cancel()
	<-receiverDone
	if !mounted {
		return fmt.Errorf("mount failed: check WinFsp/FUSE and mountpoint availability")
	}
	slog.Info("bridge stopped")
	return nil
}
