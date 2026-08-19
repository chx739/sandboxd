package diagnostic

import (
	"encoding/json"
	"fmt"
)

const maxSummarizedPods = 20

type rawPodList struct {
	Items []struct {
		Metadata struct {
			Name string `json:"name"`
		} `json:"metadata"`
		Status struct {
			Phase             string `json:"phase"`
			ContainerStatuses []struct {
				RestartCount int32 `json:"restartCount"`
			} `json:"containerStatuses"`
		} `json:"status"`
	} `json:"items"`
}

type podListSummary struct {
	Items []podSummary `json:"items"`
}

type podSummary struct {
	Metadata struct {
		Name string `json:"name"`
	} `json:"metadata"`
	Status struct {
		Phase        string `json:"phase"`
		RestartCount int32  `json:"restartCount"`
	} `json:"status"`
}

// SummarizePodList 在可信边界内压缩原始 Kubernetes PodList。
// Agent 只需要定位字段，不应把完整 PodSpec 和非必要元数据塞入上下文。
func SummarizePodList(value string) (string, error) {
	var source rawPodList
	if err := json.Unmarshal([]byte(value), &source); err != nil {
		return "", fmt.Errorf("解析 Kubernetes PodList: %w", err)
	}

	count := len(source.Items)
	if count > maxSummarizedPods {
		count = maxSummarizedPods
	}
	result := podListSummary{Items: make([]podSummary, 0, count)}
	for _, pod := range source.Items[:count] {
		var item podSummary
		item.Metadata.Name = pod.Metadata.Name
		item.Status.Phase = pod.Status.Phase
		for _, container := range pod.Status.ContainerStatuses {
			item.Status.RestartCount += container.RestartCount
		}
		result.Items = append(result.Items, item)
	}

	encoded, err := json.Marshal(result)
	if err != nil {
		return "", fmt.Errorf("编码 PodList 摘要: %w", err)
	}
	return string(encoded), nil
}
