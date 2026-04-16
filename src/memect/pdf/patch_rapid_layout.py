from typing import Any, Dict, List, Tuple

from rapid_layout.inference_engine.onnxruntime.provider_config import ProviderConfig, EP


def get_ep_list(self: ProviderConfig) -> List[Tuple[str, Dict[str, Any]]]:
    #TODO 在apple x64上测试，错误很多
    #在apple m 系列上，没有测试过，不知道
    if self.default_provider == "CoreMLExecutionProvider":
        results = [
            (
                self.default_provider,
                {
                    "MLComputeUnits": "ALL",  # 可选: "CPUOnly", "CPUAndGPU", "CPUAndNeuralEngine", "ALL"
                    #"ModelFormat": "MLProgram",
                },
            )
        ]
    else:
        results = [(EP.CPU_EP.value, self.cpu_ep_cfg())]

    if self.is_cuda_available():
        results.insert(0, (EP.CUDA_EP.value, self.cuda_ep_cfg()))

    if self.is_dml_available():
        self.logger.info(
            "Windows 10 or above detected, try to use DirectML as primary provider"
        )
        results.insert(0, (EP.DIRECTML_EP.value, self.dml_ep_cfg()))

    if self.is_cann_available():
        self.logger.info("Try to use CANNExecutionProvider to infer")
        results.insert(0, (EP.CANN_EP.value, self.cann_ep_cfg()))

    return results

ProviderConfig.get_ep_list=get_ep_list
