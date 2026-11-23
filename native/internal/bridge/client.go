package bridge

import (
	"context"
	"fmt"
	"io"
	"time"

	pb "anyvfs/native/internal/rpc"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/metadata"
)

type controllerClient struct {
	conn           *grpc.ClientConn
	client         pb.ControllerClient
	token          string
	startupTimeout time.Duration
}

func newControllerClient(input programInput) (*controllerClient, error) {
	conn, err := grpc.NewClient(input.endpoint, grpc.WithTransportCredentials(insecure.NewCredentials()),
		grpc.WithNoProxy(), grpc.WithDisableRetry(), grpc.WithDefaultCallOptions(
			grpc.MaxCallRecvMsgSize(input.maxMessageBytes), grpc.MaxCallSendMsgSize(input.maxMessageBytes)))
	if err != nil {
		return nil, err
	}
	return &controllerClient{
		conn:           conn,
		client:         pb.NewControllerClient(conn),
		token:          input.token,
		startupTimeout: input.startupTimeout,
	}, nil
}

func (c *controllerClient) Startup(ctx context.Context) (*pb.Config, []*pb.BootstrapItem, error) {
	startup, cancel := context.WithTimeout(ctx, c.startupTimeout)
	defer cancel()
	startup = metadata.AppendToOutgoingContext(startup, "authorization", "Bearer "+c.token)
	bootstrap, err := c.client.Bootstrap(startup, &pb.Empty{})
	if err != nil {
		return nil, nil, err
	}
	first, err := bootstrap.Recv()
	if err != nil {
		return nil, nil, err
	}
	config := first.GetConfig()
	if config == nil {
		return nil, nil, fmt.Errorf("first bootstrap item must be config")
	}
	var entries []*pb.BootstrapItem
	for {
		item, err := bootstrap.Recv()
		if err == io.EOF {
			return config, entries, nil
		}
		if err != nil {
			return nil, nil, err
		}
		entries = append(entries, item)
	}
}

func (c *controllerClient) Session(ctx context.Context) (pb.Controller_SessionClient, error) {
	ctx = metadata.AppendToOutgoingContext(ctx, "authorization", "Bearer "+c.token)
	return c.client.Session(ctx)
}

func (c *controllerClient) Close() error {
	return c.conn.Close()
}
