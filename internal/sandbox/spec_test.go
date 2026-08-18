package sandbox

import (
	"testing"

	corev1 "k8s.io/api/core/v1"
)

func TestBuildPodSecurityBaseline(t *testing.T) {
	pod := BuildPod(Config{
		Namespace:    "sandboxd-demo",
		Image:        "busybox:1.37.0",
		RuntimeClass: "gvisor",
	}, "test-id")

	if pod.Spec.RuntimeClassName == nil || *pod.Spec.RuntimeClassName != "gvisor" {
		t.Fatal("必须显式选择 gvisor RuntimeClass")
	}
	if pod.Spec.AutomountServiceAccountToken == nil || *pod.Spec.AutomountServiceAccountToken {
		t.Fatal("必须关闭默认 ServiceAccount token 自动挂载")
	}
	if pod.Spec.ActiveDeadlineSeconds == nil {
		t.Fatal("必须设置 Pod 最长存活时间")
	}
	if pod.Spec.SecurityContext == nil || pod.Spec.SecurityContext.RunAsNonRoot == nil || !*pod.Spec.SecurityContext.RunAsNonRoot {
		t.Fatal("必须以非 root 用户运行")
	}
	if pod.Spec.SecurityContext.SeccompProfile == nil || pod.Spec.SecurityContext.SeccompProfile.Type != corev1.SeccompProfileTypeRuntimeDefault {
		t.Fatal("必须启用 RuntimeDefault seccomp")
	}

	container := pod.Spec.Containers[0]
	if container.SecurityContext == nil {
		t.Fatal("容器安全上下文不能为空")
	}
	if container.SecurityContext.AllowPrivilegeEscalation == nil || *container.SecurityContext.AllowPrivilegeEscalation {
		t.Fatal("必须禁止权限提升")
	}
	if container.SecurityContext.ReadOnlyRootFilesystem == nil || !*container.SecurityContext.ReadOnlyRootFilesystem {
		t.Fatal("根文件系统必须只读")
	}
	if !containsCapability(container.SecurityContext.Capabilities.Drop, "ALL") {
		t.Fatal("必须 drop ALL capabilities")
	}
	if container.Resources.Limits.Cpu().IsZero() || container.Resources.Limits.Memory().IsZero() {
		t.Fatal("CPU 和内存 limit 不能为空")
	}

	for _, volume := range pod.Spec.Volumes {
		if volume.EmptyDir != nil && volume.EmptyDir.SizeLimit == nil {
			t.Fatalf("emptyDir %q 必须设置 sizeLimit", volume.Name)
		}
	}
}

func TestBuildPodRuntimeClassCanBeDisabled(t *testing.T) {
	pod := BuildPod(Config{Namespace: "sandboxd-demo", Image: "busybox:1.37.0"}, "runc-test")
	if pod.Spec.RuntimeClassName != nil {
		t.Fatal("RuntimeClass 为空时不应写入 PodSpec")
	}
}

func containsCapability(values []corev1.Capability, want corev1.Capability) bool {
	for _, value := range values {
		if value == want {
			return true
		}
	}
	return false
}
