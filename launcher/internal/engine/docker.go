package engine

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strings"

	"github.com/docker/docker/api/types/image"
	"github.com/docker/docker/api/types/registry"
	"github.com/docker/docker/client"
)

var ApplicationServices = []string{"head", "bot", "ai_worker"}

type Docker interface {
	Ping(context.Context) error
	Pull(context.Context, string) error
	Close() error
}

type DockerClient struct {
	client       *client.Client
	registryAuth string
}

func NewDockerClient(namespace, token string) (*DockerClient, error) {
	api, err := client.NewClientWithOpts(client.FromEnv, client.WithAPIVersionNegotiation())
	if err != nil {
		return nil, err
	}
	parts := strings.Split(strings.TrimPrefix(namespace, "ghcr.io/"), "/")
	username := parts[0]
	auth, err := json.Marshal(registry.AuthConfig{
		Username: username, Password: token, ServerAddress: "ghcr.io",
	})
	if err != nil {
		api.Close()
		return nil, err
	}
	return &DockerClient{
		client: api, registryAuth: base64.URLEncoding.EncodeToString(auth),
	}, nil
}

func (d *DockerClient) Ping(ctx context.Context) error {
	_, err := d.client.Ping(ctx)
	return err
}

func (d *DockerClient) Pull(ctx context.Context, reference string) error {
	stream, err := d.client.ImagePull(ctx, reference, image.PullOptions{RegistryAuth: d.registryAuth})
	if err != nil {
		return fmt.Errorf("pull %s: %w", reference, err)
	}
	defer stream.Close()
	if err := consumePullResponse(stream); err != nil {
		return fmt.Errorf("pull %s failed: %w", reference, err)
	}
	return nil
}

func consumePullResponse(stream io.Reader) error {
	decoder := json.NewDecoder(stream)
	for {
		var event struct {
			Error       string `json:"error"`
			ErrorDetail struct {
				Message string `json:"message"`
			} `json:"errorDetail"`
		}
		if err := decoder.Decode(&event); errors.Is(err, io.EOF) {
			return nil
		} else if err != nil {
			return fmt.Errorf("invalid Docker pull response: %w", err)
		}
		message := event.ErrorDetail.Message
		if message == "" {
			message = event.Error
		}
		if message != "" {
			return errors.New(message)
		}
	}
}

func (d *DockerClient) Close() error {
	return d.client.Close()
}

func Images(namespace, target string) map[string]string {
	images := make(map[string]string, len(ApplicationServices))
	for _, service := range ApplicationServices {
		images[service] = fmt.Sprintf("%s/%s:%s", namespace, service, target)
	}
	return images
}
