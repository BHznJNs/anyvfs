// The controller supplies only startup input and receives typed mutation events.
package main

import (
	"context"
	"fmt"
	"log/slog"
	"os"
	"os/signal"
	"syscall"
	"time"

	"vfspy/native/internal/memfs"
	pb "vfspy/native/internal/rpc"
)

func run() error {
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
	fs, err := memfs.New(config, entries)
	if err != nil {
		return err
	}
	slog.Debug("initial data imported", "entries", len(entries))
	stream, err := client.Session(ctx)
	if err != nil {
		return err
	}
	host := newFuseHost(fs, config.CaseSensitive)
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
		for {
			command, e := stream.Recv()
			if e != nil {
				if ctx.Err() == nil {
					slog.Warn("controller session disconnected", "error", e)
				}
				return
			}
			switch v := command.Value.(type) {
			case *pb.Command_Barrier:
				fs.Barrier(v.Barrier.Id)
			case *pb.Command_Stop:
				slog.Debug("unmount requested")
				return
			default:
				slog.Warn("unknown controller command")
				return
			}
		}
	}()
	senderDone := make(chan error, 1)
	go func() {
		for {
			notice, e := fs.Next(ctx)
			if e != nil {
				senderDone <- e
				return
			}
			if e = stream.Send(notice); e != nil {
				cancel()
				senderDone <- e
				return
			}
			if notice.GetStopped() != nil {
				senderDone <- stream.CloseSend()
				return
			}
		}
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
	// Host.Mount returns only after the callbacks are quiescent. Stopped follows
	// every committed event, allowing Python to drain callbacks on clean close.
	fs.Stopped()
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

func main() {
	handler := slog.NewJSONHandler(os.Stderr, &slog.HandlerOptions{
		Level: slog.LevelDebug,
	})
	slog.SetDefault(slog.New(handler).With("pid", os.Getpid()))

	if err := run(); err != nil {
		slog.Error("bridge failed", "error", err)
		os.Exit(1)
	}
}
