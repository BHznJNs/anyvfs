package bridge

import (
	"context"
	"log/slog"

	"vfspy/native/internal/memfs"
	pb "vfspy/native/internal/rpc"
)

func receiveCommands(ctx context.Context, stream pb.Controller_SessionClient, fs *memfs.FS) {
	for {
		command, err := stream.Recv()
		if err != nil {
			if ctx.Err() == nil {
				slog.Warn("controller session disconnected", "error", err)
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
}

func sendNotices(ctx context.Context, stream pb.Controller_SessionClient, fs *memfs.FS, cancel context.CancelFunc) error {
	for {
		notice, err := fs.Next(ctx)
		if err != nil {
			return err
		}
		if err = stream.Send(notice); err != nil {
			cancel()
			return err
		}
		if notice.GetStopped() != nil {
			return stream.CloseSend()
		}
	}
}
