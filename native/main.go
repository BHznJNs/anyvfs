// The controller supplies only startup input and receives typed mutation events.
package main

import (
	"context"
	"fmt"
	"io"
	"log/slog"
	"os"
	"os/signal"
	"syscall"
	"time"

	"vfspy/native/internal/memfs"
	pb "vfspy/native/internal/rpc"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/metadata"
)

func run() error {
	input, err := parseInput()
	if err != nil {
		return err
	}

	slog.SetDefault(slog.Default().With("mountpoint", input.mountpoint))
	slog.Info("bridge starting")
	conn, err := grpc.NewClient(input.endpoint, grpc.WithTransportCredentials(insecure.NewCredentials()),
		grpc.WithNoProxy(), grpc.WithDisableRetry(), grpc.WithDefaultCallOptions(
			grpc.MaxCallRecvMsgSize(input.maxMessageBytes), grpc.MaxCallSendMsgSize(input.maxMessageBytes)))
	if err != nil {
		return err
	}
	defer conn.Close()
	client := pb.NewControllerClient(conn)
	ctx, cancel := context.WithCancel(metadata.AppendToOutgoingContext(context.Background(), "authorization", "Bearer "+input.token))
	defer cancel()
	startup, cancelStartup := context.WithTimeout(ctx, input.startupTimeout)
	bootstrap, err := client.Bootstrap(startup, &pb.Empty{})
	if err != nil {
		cancelStartup()
		return err
	}
	first, err := bootstrap.Recv()
	if err != nil {
		cancelStartup()
		return err
	}
	fs, err := memfs.New(first.GetConfig())
	if err != nil {
		cancelStartup()
		return err
	}
	entries := 0
	for {
		item, e := bootstrap.Recv()
		if e == io.EOF {
			break
		}
		if e != nil {
			cancelStartup()
			return e
		}
		if e = fs.AddInitialEntry(item.GetEntry()); e != nil {
			cancelStartup()
			return e
		}
		entries++
	}
	cancelStartup()
	slog.Debug("initial data imported", "entries", entries)
	stream, err := client.Session(ctx)
	if err != nil {
		return err
	}
	host := newFuseHost(fs, first.GetConfig().CaseSensitive)
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
