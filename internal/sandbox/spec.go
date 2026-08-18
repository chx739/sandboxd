package sandbox

import (
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

const (
	serviceAccountMountPath = "/var/run/secrets/kubernetes.io/serviceaccount"
	workspaceMountPath      = "/workspace"
	tmpMountPath            = "/tmp"
)

// BuildPod 是沙箱安全约束的唯一入口。调用方只负责提交这个 Pod，不能再零散修改 PodSpec。
func BuildPod(cfg Config, id string) *corev1.Pod {
	runAsNonRoot := true
	allowPrivilegeEscalation := false
	readOnlyRootFilesystem := true
	automountServiceAccountToken := false
	enableServiceLinks := false
	runAsID := int64(65532)
	activeDeadlineSeconds := int64(3600)
	terminationGracePeriodSeconds := int64(5)
	projectedDefaultMode := int32(0o444)
	tokenExpirationSeconds := int64(3600)

	pod := &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "sandbox-" + id,
			Namespace: cfg.Namespace,
			Labels: map[string]string{
				LabelManagedBy: ValueManagedBy,
				LabelState:     string(StateIdle),
				LabelID:        id,
			},
		},
		Spec: corev1.PodSpec{
			ServiceAccountName:            ServiceAccountName,
			AutomountServiceAccountToken:  &automountServiceAccountToken,
			RestartPolicy:                 corev1.RestartPolicyNever,
			ActiveDeadlineSeconds:         &activeDeadlineSeconds,
			TerminationGracePeriodSeconds: &terminationGracePeriodSeconds,
			EnableServiceLinks:            &enableServiceLinks,
			SecurityContext: &corev1.PodSecurityContext{
				RunAsNonRoot:   &runAsNonRoot,
				RunAsUser:      &runAsID,
				RunAsGroup:     &runAsID,
				FSGroup:        &runAsID,
				SeccompProfile: &corev1.SeccompProfile{Type: corev1.SeccompProfileTypeRuntimeDefault},
			},
			Containers: []corev1.Container{
				{
					Name:    "sandbox",
					Image:   cfg.Image,
					Command: []string{"sleep", "infinity"},
					SecurityContext: &corev1.SecurityContext{
						AllowPrivilegeEscalation: &allowPrivilegeEscalation,
						ReadOnlyRootFilesystem:   &readOnlyRootFilesystem,
						Capabilities: &corev1.Capabilities{
							Drop: []corev1.Capability{"ALL"},
						},
					},
					Resources: corev1.ResourceRequirements{
						Requests: corev1.ResourceList{
							corev1.ResourceCPU:    resource.MustParse("100m"),
							corev1.ResourceMemory: resource.MustParse("128Mi"),
						},
						Limits: corev1.ResourceList{
							corev1.ResourceCPU:    resource.MustParse("500m"),
							corev1.ResourceMemory: resource.MustParse("256Mi"),
						},
					},
					Env: []corev1.EnvVar{{Name: "HOME", Value: workspaceMountPath}},
					VolumeMounts: []corev1.VolumeMount{
						{Name: "kube-api-access", MountPath: serviceAccountMountPath, ReadOnly: true},
						{Name: "workspace", MountPath: workspaceMountPath},
						{Name: "tmp", MountPath: tmpMountPath},
					},
				},
			},
			Volumes: []corev1.Volume{
				{
					Name: "kube-api-access",
					VolumeSource: corev1.VolumeSource{Projected: &corev1.ProjectedVolumeSource{
						DefaultMode: &projectedDefaultMode,
						Sources: []corev1.VolumeProjection{
							{ServiceAccountToken: &corev1.ServiceAccountTokenProjection{
								Path:              "token",
								ExpirationSeconds: &tokenExpirationSeconds,
							}},
							{ConfigMap: &corev1.ConfigMapProjection{
								LocalObjectReference: corev1.LocalObjectReference{Name: "kube-root-ca.crt"},
								Items:                []corev1.KeyToPath{{Key: "ca.crt", Path: "ca.crt"}},
							}},
							{DownwardAPI: &corev1.DownwardAPIProjection{
								Items: []corev1.DownwardAPIVolumeFile{{
									Path: "namespace",
									FieldRef: &corev1.ObjectFieldSelector{
										APIVersion: "v1",
										FieldPath:  "metadata.namespace",
									},
								}},
							}},
						},
					}},
				},
				{
					Name: "workspace",
					VolumeSource: corev1.VolumeSource{EmptyDir: &corev1.EmptyDirVolumeSource{
						SizeLimit: quantity("64Mi"),
					}},
				},
				{
					Name: "tmp",
					VolumeSource: corev1.VolumeSource{EmptyDir: &corev1.EmptyDirVolumeSource{
						Medium:    corev1.StorageMediumMemory,
						SizeLimit: quantity("32Mi"),
					}},
				},
			},
		},
	}

	// RuntimeClass 为空时不设置字段，方便在没有 gVisor 的环境做纯逻辑开发。
	if cfg.RuntimeClass != "" {
		pod.Spec.RuntimeClassName = &cfg.RuntimeClass
	}

	return pod
}

func quantity(value string) *resource.Quantity {
	result := resource.MustParse(value)
	return &result
}
