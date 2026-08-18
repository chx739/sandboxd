package sandbox

import "testing"

func TestBuildPodUsesControlledTokenAndBoundedWritableVolumes(t *testing.T) {
	pod := BuildPod(Config{Namespace: "sandboxd-demo", Image: "busybox:1.37.0"}, "volume-test")

	if pod.Spec.ServiceAccountName != ServiceAccountName {
		t.Fatalf("ServiceAccount = %q，期望 %q", pod.Spec.ServiceAccountName, ServiceAccountName)
	}

	emptyDirCount := 0
	foundProjectedToken := false
	for _, volume := range pod.Spec.Volumes {
		if volume.EmptyDir != nil {
			emptyDirCount++
			if volume.EmptyDir.SizeLimit == nil || volume.EmptyDir.SizeLimit.IsZero() {
				t.Fatalf("emptyDir %q 必须有非零 sizeLimit", volume.Name)
			}
		}
		if volume.Projected != nil {
			for _, source := range volume.Projected.Sources {
				if source.ServiceAccountToken == nil {
					continue
				}
				foundProjectedToken = source.ServiceAccountToken.ExpirationSeconds != nil &&
					*source.ServiceAccountToken.ExpirationSeconds == 3600
			}
		}
	}

	if emptyDirCount != 2 {
		t.Fatalf("emptyDir 数量 = %d，期望 workspace 和 tmp 两个", emptyDirCount)
	}
	if !foundProjectedToken {
		t.Fatal("必须显式投影 1 小时有效期的 ServiceAccount token")
	}
}
