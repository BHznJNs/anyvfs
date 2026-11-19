package bridge

import (
	"context"
	"log/slog"

	"anyvfs/native/internal/queue"
	pb "anyvfs/native/internal/rpc"
)

func receiveCommand(ctx context.Context, stream pb.Controller_SessionClient) {
	command, err := stream.Recv()
	if err != nil {
		if ctx.Err() == nil {
			slog.Warn("controller session disconnected", "error", err)
		}
		return
	}
	if command.GetStop() != nil {
		slog.Debug("unmount requested")
		return
	}
	slog.Warn("unknown controller command")
}

func sendNotices(ctx context.Context, stream pb.Controller_SessionClient, notices *queue.Queue[*pb.Notice], cancel context.CancelFunc) error {
	for {
		notice, err := notices.Pop(ctx)
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
