package diagnostic

import (
	"errors"
	"strings"
	"testing"
)

func TestBuildKubernetesCommand(t *testing.T) {
	tests := []struct {
		name     string
		request  KubernetesRequest
		endpoint string
	}{
		{
			name: "list pods",
			request: KubernetesRequest{
				Operation: OperationListPods,
				Namespace: "sandboxd-target",
			},
			endpoint: "https://kubernetes.default.svc/api/v1/namespaces/sandboxd-target/pods",
		},
		{
			name: "get deployment",
			request: KubernetesRequest{
				Operation: OperationGetDeployment,
				Namespace: "sandboxd-target",
				Name:      "crashloop-demo",
			},
			endpoint: "https://kubernetes.default.svc/apis/apps/v1/namespaces/sandboxd-target/deployments/crashloop-demo",
		},
		{
			name: "pod logs with bounded options",
			request: KubernetesRequest{
				Operation: OperationGetPodLogs,
				Namespace: "sandboxd-target",
				Name:      "crashloop-demo-abcde",
				Container: "app",
				TailLines: 200,
				Previous:  true,
			},
			endpoint: "https://kubernetes.default.svc/api/v1/namespaces/sandboxd-target/pods/crashloop-demo-abcde/log?container=app&previous=true&tailLines=200",
		},
		{
			name: "pod logs use safe default tail",
			request: KubernetesRequest{
				Operation: OperationGetPodLogs,
				Namespace: "sandboxd-target",
				Name:      "crashloop-demo-abcde",
			},
			endpoint: "https://kubernetes.default.svc/api/v1/namespaces/sandboxd-target/pods/crashloop-demo-abcde/log?tailLines=100",
		},
		{
			name: "get configmap",
			request: KubernetesRequest{
				Operation: OperationGetConfigMap,
				Namespace: "sandboxd-target",
				Name:      "crashloop-demo-config",
			},
			endpoint: "https://kubernetes.default.svc/api/v1/namespaces/sandboxd-target/configmaps/crashloop-demo-config",
		},
		{
			name: "events for one object",
			request: KubernetesRequest{
				Operation: OperationListEvents,
				Namespace: "sandboxd-target",
				Name:      "crashloop-demo-abcde",
			},
			endpoint: "https://kubernetes.default.svc/api/v1/namespaces/sandboxd-target/events?fieldSelector=involvedObject.name%3Dcrashloop-demo-abcde",
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			command, err := BuildKubernetesCommand(test.request, "sandboxd-target")
			if err != nil {
				t.Fatalf("BuildKubernetesCommand() error = %v", err)
			}
			if len(command) != 5 {
				t.Fatalf("command length = %d, want 5: %#v", len(command), command)
			}
			if command[0] != "sh" || command[1] != "-ceu" {
				t.Fatalf("command does not use fixed shell wrapper: %#v", command)
			}
			if command[4] != test.endpoint {
				t.Fatalf("endpoint = %q, want %q", command[4], test.endpoint)
			}
			if strings.Contains(command[2], test.request.Name) && test.request.Name != "" {
				t.Fatalf("model-controlled name leaked into shell program: %q", command[2])
			}
		})
	}
}

func TestBuildKubernetesCommandRejectsUnsafeInput(t *testing.T) {
	tests := []struct {
		name    string
		request KubernetesRequest
		target  error
	}{
		{
			name: "write operation",
			request: KubernetesRequest{
				Operation: "delete_namespace",
				Namespace: "sandboxd-target",
				Name:      "sandboxd-target",
			},
			target: ErrOperationDenied,
		},
		{
			name: "different namespace",
			request: KubernetesRequest{
				Operation: OperationListPods,
				Namespace: "kube-system",
			},
			target: ErrNamespaceDenied,
		},
		{
			name: "invalid object name",
			request: KubernetesRequest{
				Operation: OperationGetConfigMap,
				Namespace: "sandboxd-target",
				Name:      "../../secrets",
			},
			target: ErrInvalidArgument,
		},
		{
			name: "tail too large",
			request: KubernetesRequest{
				Operation: OperationGetPodLogs,
				Namespace: "sandboxd-target",
				Name:      "crashloop-demo-abcde",
				TailLines: 201,
			},
			target: ErrInvalidArgument,
		},
		{
			name: "irrelevant log fields",
			request: KubernetesRequest{
				Operation: OperationGetDeployment,
				Namespace: "sandboxd-target",
				Name:      "crashloop-demo",
				Previous:  true,
			},
			target: ErrInvalidArgument,
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			_, err := BuildKubernetesCommand(test.request, "sandboxd-target")
			if !errors.Is(err, test.target) {
				t.Fatalf("error = %v, want errors.Is(%v)", err, test.target)
			}
		})
	}
}
