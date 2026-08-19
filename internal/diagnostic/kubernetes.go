package diagnostic

import (
	"errors"
	"fmt"
	"net/url"

	"k8s.io/apimachinery/pkg/util/validation"
)

const (
	OperationListPods      = "list_pods"
	OperationGetDeployment = "get_deployment"
	OperationGetPodLogs    = "get_pod_logs"
	OperationGetConfigMap  = "get_configmap"
	OperationListEvents    = "list_events"
)

var (
	ErrOperationDenied = errors.New("operation 不在只读白名单")
	ErrNamespaceDenied = errors.New("namespace 不在诊断范围")
	ErrInvalidArgument = errors.New("诊断参数无效")
)

const kubernetesReadScript = "token_file=/var/run/secrets/kubernetes.io/serviceaccount/token\n" +
	"ca_file=/var/run/secrets/kubernetes.io/serviceaccount/ca.crt\n" +
	"token=\"$(cat \"$token_file\")\"\n" +
	"printf 'header = \"Authorization: Bearer %s\"\\n' \"$token\" | " +
	"curl --config - --fail-with-body --silent --show-error --request GET " +
	"--cacert \"$ca_file\" --header 'Accept: application/json' " +
	"--connect-timeout 5 --max-time 20 \"$1\"\n"

// KubernetesRequest 是 Agent 唯一能提交的 Kubernetes 诊断参数。
// 它不包含 URL、Header、Token 或 argv，避免把执行细节交给不可信模型。
type KubernetesRequest struct {
	Operation string `json:"operation"`
	Namespace string `json:"namespace"`
	Name      string `json:"name,omitempty"`
	Container string `json:"container,omitempty"`
	TailLines int    `json:"tailLines,omitempty"`
	Previous  bool   `json:"previous,omitempty"`
}

// BuildKubernetesCommand 把结构化读请求转换成固定命令。
// shell 程序文本完全由服务端定义；经校验的模型参数只出现在 URL 参数 $1 中。
func BuildKubernetesCommand(request KubernetesRequest, allowedNamespace string) ([]string, error) {
	path, err := buildKubernetesPath(request, allowedNamespace)
	if err != nil {
		return nil, err
	}
	endpoint := "https://kubernetes.default.svc" + path
	return []string{"sh", "-ceu", kubernetesReadScript, "sandboxd-kubernetes-read", endpoint}, nil
}

func buildKubernetesPath(request KubernetesRequest, allowedNamespace string) (string, error) {
	if allowedNamespace == "" || request.Namespace != allowedNamespace {
		return "", fmt.Errorf("%w: %q", ErrNamespaceDenied, request.Namespace)
	}
	if problems := validation.IsDNS1123Label(request.Namespace); len(problems) > 0 {
		return "", fmt.Errorf("%w: namespace: %s", ErrInvalidArgument, problems[0])
	}
	if request.Name != "" {
		if problems := validation.IsDNS1123Subdomain(request.Name); len(problems) > 0 {
			return "", fmt.Errorf("%w: name: %s", ErrInvalidArgument, problems[0])
		}
	}
	if request.Container != "" {
		if problems := validation.IsDNS1123Label(request.Container); len(problems) > 0 {
			return "", fmt.Errorf("%w: container: %s", ErrInvalidArgument, problems[0])
		}
	}

	namespace := url.PathEscape(request.Namespace)
	coreBase := "/api/v1/namespaces/" + namespace

	switch request.Operation {
	case OperationListPods:
		if request.Name != "" || hasLogOptions(request) {
			return "", fmt.Errorf("%w: list_pods 不接受 name 或日志参数", ErrInvalidArgument)
		}
		return coreBase + "/pods", nil

	case OperationGetDeployment:
		if err := requireNamedObject(request, "get_deployment"); err != nil {
			return "", err
		}
		return "/apis/apps/v1/namespaces/" + namespace + "/deployments/" + url.PathEscape(request.Name), nil

	case OperationGetConfigMap:
		if err := requireNamedObject(request, "get_configmap"); err != nil {
			return "", err
		}
		return coreBase + "/configmaps/" + url.PathEscape(request.Name), nil

	case OperationGetPodLogs:
		if request.Name == "" {
			return "", fmt.Errorf("%w: get_pod_logs 必须提供 name", ErrInvalidArgument)
		}
		tailLines := request.TailLines
		if tailLines == 0 {
			tailLines = 100
		}
		if tailLines < 1 || tailLines > 200 {
			return "", fmt.Errorf("%w: tailLines 必须在 1 到 200 之间", ErrInvalidArgument)
		}
		query := url.Values{}
		query.Set("tailLines", fmt.Sprintf("%d", tailLines))
		if request.Container != "" {
			query.Set("container", request.Container)
		}
		if request.Previous {
			query.Set("previous", "true")
		}
		return coreBase + "/pods/" + url.PathEscape(request.Name) + "/log?" + query.Encode(), nil

	case OperationListEvents:
		if hasLogOptions(request) {
			return "", fmt.Errorf("%w: list_events 不接受日志参数", ErrInvalidArgument)
		}
		if request.Name == "" {
			return coreBase + "/events", nil
		}
		query := url.Values{}
		query.Set("fieldSelector", "involvedObject.name="+request.Name)
		return coreBase + "/events?" + query.Encode(), nil

	default:
		return "", fmt.Errorf("%w: %q", ErrOperationDenied, request.Operation)
	}
}

func requireNamedObject(request KubernetesRequest, operation string) error {
	if request.Name == "" {
		return fmt.Errorf("%w: %s 必须提供 name", ErrInvalidArgument, operation)
	}
	if hasLogOptions(request) {
		return fmt.Errorf("%w: %s 不接受日志参数", ErrInvalidArgument, operation)
	}
	return nil
}

func hasLogOptions(request KubernetesRequest) bool {
	return request.Container != "" || request.TailLines != 0 || request.Previous
}
