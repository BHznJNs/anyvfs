// The controller supplies only startup input and receives typed mutation events.
package main

import (
	"context"
	"flag"
	"fmt"
	"io"
	"log/slog"
	"net"
	"os"
	"os/signal"
	"runtime"
	"syscall"
	"time"

	"github.com/winfsp/cgofuse/fuse"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/metadata"
	"vfspy/native/internal/memfs"
	pb "vfspy/native/internal/rpc"
)

func run() error {
	endpoint := flag.String("endpoint", "", "Python controller loopback address")
	mountpoint := flag.String("mountpoint", "", "mount path or unused Windows drive")
	maxMessage := flag.Int("max-message-bytes", 16<<20, "gRPC message size")
	timeout := flag.Duration("startup-timeout", 25*time.Second, "bootstrap deadline")
	flag.Parse()
	slog.SetDefault(slog.Default().With("mountpoint", *mountpoint))
	token := os.Getenv("VFSPY_TOKEN")
	os.Unsetenv("VFSPY_TOKEN")
	hostIP, _, err := net.SplitHostPort(*endpoint)
	if err != nil || hostIP != "127.0.0.1" || token == "" || *mountpoint == "" || *timeout <= 0 {
		return fmt.Errorf("loopback endpoint, token, mountpoint and positive startup timeout required")
	}
	slog.Info("bridge starting")
	conn, err := grpc.NewClient(*endpoint, grpc.WithTransportCredentials(insecure.NewCredentials()),
		grpc.WithNoProxy(), grpc.WithDisableRetry(), grpc.WithDefaultCallOptions(
			grpc.MaxCallRecvMsgSize(*maxMessage), grpc.MaxCallSendMsgSize(*maxMessage)))
	if err != nil {
		return err
	}
	defer conn.Close()
	client := pb.NewControllerClient(conn)
	ctx, cancel := context.WithCancel(metadata.AppendToOutgoingContext(context.Background(), "authorization", "Bearer "+token))
	defer cancel()
	startup, cancelStartup := context.WithTimeout(ctx, *timeout)
	seed, err := client.Bootstrap(startup, &pb.Empty{})
	if err != nil {
		cancelStartup()
		return err
	}
	first, err := seed.Recv()
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
		item, e := seed.Recv()
		if e == io.EOF {
			break
		}
		if e != nil {
			cancelStartup()
			return e
		}
		if e = fs.Seed(item.GetEntry()); e != nil {
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
	host := fuse.NewFileSystemHost(fs)
	host.SetCapCaseInsensitive(!first.GetConfig().CaseSensitive)
	host.SetCapReaddirPlus(true)
	host.SetCapOpenTrunc(true)
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
	options := []string{"-f", "-o", "volname=vfspy", "-o", "attr_timeout=0", "-o", "entry_timeout=0", "-o", "negative_timeout=0"}
	if runtime.GOOS == "windows" {
		options = append(options, "-o", "uid=-1,gid=-1")
	}
	mounted := host.Mount(*mountpoint, options)
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
	slog.SetDefault(slog.New(slog.NewJSONHandler(os.Stderr, &slog.HandlerOptions{
		Level: slog.LevelDebug,
	})).With("pid", os.Getpid()))
	if err := run(); err != nil {
		slog.Error("bridge failed", "error", err)
		os.Exit(1)
	}
}
