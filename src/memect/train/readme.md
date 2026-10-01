
PaddlePaddle的新版本>=2.6，生成的是PIR图，且version=4，老版本<=2.5.2，生成version=1

当使用新的PaddlePaddle训练后，得到的inference.json+inference.pdiparams不一定能够输出onnx

就需要使用老版本的PaddlePaddle去训练，如果不想，就需要

获得老版本生成的renference.json和新版本生成的reference.json进行对比，然后看是否可以修改为一致

最根本的原因是：paddle2onnx没有同步更新，也就是仅仅支持比较老的模型

