"""
TransNetV2模型封装 - 基于深度学习的转场检测

主要功能：
1. TransNetV2模型的PyTorch实现
2. 视频转场检测和预测
3. 模型加载和推理

作者：重构版本
日期：2025-06-19
基于：https://github.com/soCzech/TransNetV2
"""

import logging
import subprocess
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from video_fission import media

logger = logging.getLogger(__name__)


class TransNetV2(nn.Module):
    """
    TransNetV2神经网络模型

    用于视频转场检测的深度学习模型
    """

    def __init__(
        self,
        F=16,
        L=3,
        S=2,
        D=1024,
        use_many_hot_targets=True,
        use_frame_similarity=True,
        use_color_histograms=True,
        use_mean_pooling=False,
        dropout_rate=0.5,
        use_convex_comb_reg=False,
        use_resnet_features=False,
        use_resnet_like_top=False,
        frame_similarity_on_last_layer=False,
    ):
        """
        初始化TransNetV2模型

        Args:
            F: 基础滤波器数量
            L: 层数
            S: 每层的块数
            D: 全连接层维度
            use_many_hot_targets: 是否使用多热目标
            use_frame_similarity: 是否使用帧相似性
            use_color_histograms: 是否使用颜色直方图
            use_mean_pooling: 是否使用平均池化
            dropout_rate: Dropout率
            其他参数: 暂不支持的功能
        """
        super().__init__()

        # 检查不支持的功能
        if use_resnet_features or use_resnet_like_top or use_convex_comb_reg or frame_similarity_on_last_layer:
            raise NotImplementedError("某些选项在PyTorch版本中未实现!")

        # 构建堆叠的DDCNN层
        self.SDDCNN = nn.ModuleList(
            [StackedDDCNNV2(in_filters=3, n_blocks=S, filters=F, stochastic_depth_drop_prob=0.0)]
            + [StackedDDCNNV2(in_filters=(F * 2 ** (i - 1)) * 4, n_blocks=S, filters=F * 2**i) for i in range(1, L)]
        )

        # 帧相似性层
        self.frame_sim_layer = (
            FrameSimilarity(
                sum([(F * 2**i) * 4 for i in range(L)]),
                lookup_window=101,
                output_dim=128,
                similarity_dim=128,
                use_bias=True,
            )
            if use_frame_similarity
            else None
        )

        # 颜色直方图层
        self.color_hist_layer = ColorHistograms(lookup_window=101, output_dim=128) if use_color_histograms else None

        # Dropout层
        self.dropout = nn.Dropout(dropout_rate) if dropout_rate is not None else None

        # 计算输出维度
        output_dim = ((F * 2 ** (L - 1)) * 4) * 3 * 6  # 3x6为空间维度
        if use_frame_similarity:
            output_dim += 128
        if use_color_histograms:
            output_dim += 128

        # 全连接层
        self.fc1 = nn.Linear(output_dim, D)
        self.cls_layer1 = nn.Linear(D, 1)
        self.cls_layer2 = nn.Linear(D, 1) if use_many_hot_targets else None

        self.use_mean_pooling = use_mean_pooling
        self.eval()

    def forward(self, inputs):
        """
        前向传播

        Args:
            inputs: 输入张量 [B, T, H, W, 3]

        Returns:
            转场预测结果
        """
        # 验证输入格式
        assert (
            isinstance(inputs, torch.Tensor) and list(inputs.shape[2:]) == [27, 48, 3] and inputs.dtype == torch.uint8
        ), "输入格式不正确"

        # 转换格式：uint8 [B, T, H, W, 3] -> float [B, 3, T, H, W]
        x = inputs.permute([0, 4, 1, 2, 3]).float()
        x = x.div_(255.0)

        # 通过SDDCNN层
        block_features = []
        for block in self.SDDCNN:
            x = block(x)
            block_features.append(x)

        # 池化操作
        if self.use_mean_pooling:
            x = torch.mean(x, dim=[3, 4])
            x = x.permute(0, 2, 1)
        else:
            x = x.permute(0, 2, 3, 4, 1)
            x = x.reshape(x.shape[0], x.shape[1], -1)

        # 添加帧相似性特征
        if self.frame_sim_layer is not None:
            x = torch.cat([self.frame_sim_layer(block_features), x], 2)

        # 添加颜色直方图特征
        if self.color_hist_layer is not None:
            x = torch.cat([self.color_hist_layer(inputs), x], 2)

        # 全连接层
        x = self.fc1(x)
        x = F.relu(x)

        if self.dropout is not None:
            x = self.dropout(x)

        one_hot = self.cls_layer1(x)

        if self.cls_layer2 is not None:
            return one_hot, {"many_hot": self.cls_layer2(x)}

        return one_hot


class StackedDDCNNV2(nn.Module):
    """堆叠的扩张密集卷积网络V2"""

    def __init__(
        self,
        in_filters,
        n_blocks,
        filters,
        shortcut=True,
        use_octave_conv=False,
        pool_type="avg",
        stochastic_depth_drop_prob=0.0,
    ):
        super().__init__()

        if use_octave_conv:
            raise NotImplementedError("Octave卷积在PyTorch版本中未实现!")

        assert pool_type in ["max", "avg"]

        self.shortcut = shortcut
        self.DDCNN = nn.ModuleList(
            [
                DilatedDCNNV2(
                    in_filters if i == 1 else filters * 4,
                    filters,
                    octave_conv=use_octave_conv,
                    activation=F.relu if i != n_blocks else None,
                )
                for i in range(1, n_blocks + 1)
            ]
        )

        self.pool = nn.MaxPool3d(kernel_size=(1, 2, 2)) if pool_type == "max" else nn.AvgPool3d(kernel_size=(1, 2, 2))
        self.stochastic_depth_drop_prob = stochastic_depth_drop_prob

    def forward(self, inputs):
        x = inputs
        shortcut = None

        for block in self.DDCNN:
            x = block(x)
            if shortcut is None:
                shortcut = x

        x = F.relu(x)

        if self.shortcut is not None:
            if self.stochastic_depth_drop_prob != 0.0:
                if self.training:
                    if torch.rand(1).item() < self.stochastic_depth_drop_prob:
                        x = shortcut
                    else:
                        x = x + shortcut
                else:
                    x = (1 - self.stochastic_depth_drop_prob) * x + shortcut
            else:
                x += shortcut

        x = self.pool(x)
        return x


class DilatedDCNNV2(nn.Module):
    """扩张密集卷积网络V2"""

    def __init__(self, in_filters, filters, batch_norm=True, activation=None, octave_conv=False):
        super().__init__()

        if octave_conv:
            raise NotImplementedError("Octave卷积在PyTorch版本中未实现!")

        self.Conv3D_1 = Conv3DConfigurable(in_filters, filters, 1, use_bias=not batch_norm)
        self.Conv3D_2 = Conv3DConfigurable(in_filters, filters, 2, use_bias=not batch_norm)
        self.Conv3D_4 = Conv3DConfigurable(in_filters, filters, 4, use_bias=not batch_norm)
        self.Conv3D_8 = Conv3DConfigurable(in_filters, filters, 8, use_bias=not batch_norm)

        self.bn = nn.BatchNorm3d(filters * 4, eps=1e-3) if batch_norm else None
        self.activation = activation

    def forward(self, inputs):
        conv1 = self.Conv3D_1(inputs)
        conv2 = self.Conv3D_2(inputs)
        conv3 = self.Conv3D_4(inputs)
        conv4 = self.Conv3D_8(inputs)

        x = torch.cat([conv1, conv2, conv3, conv4], dim=1)

        if self.bn is not None:
            x = self.bn(x)

        if self.activation is not None:
            x = self.activation(x)

        return x


class Conv3DConfigurable(nn.Module):
    """可配置的3D卷积层"""

    def __init__(
        self, in_filters, filters, dilation_rate, separable=True, octave=False, use_bias=True, kernel_initializer=None
    ):
        super().__init__()

        if octave or kernel_initializer is not None:
            raise NotImplementedError("某些功能在PyTorch版本中未实现!")

        if separable:
            # (2+1)D卷积
            conv1 = nn.Conv3d(
                in_filters, 2 * filters, kernel_size=(1, 3, 3), dilation=(1, 1, 1), padding=(0, 1, 1), bias=False
            )
            conv2 = nn.Conv3d(
                2 * filters,
                filters,
                kernel_size=(3, 1, 1),
                dilation=(dilation_rate, 1, 1),
                padding=(dilation_rate, 0, 0),
                bias=use_bias,
            )
            self.layers = nn.ModuleList([conv1, conv2])
        else:
            conv = nn.Conv3d(
                in_filters,
                filters,
                kernel_size=3,
                dilation=(dilation_rate, 1, 1),
                padding=(dilation_rate, 1, 1),
                bias=use_bias,
            )
            self.layers = nn.ModuleList([conv])

    def forward(self, inputs):
        x = inputs
        for layer in self.layers:
            x = layer(x)
        return x


class FrameSimilarity(nn.Module):
    """帧相似性计算层"""

    def __init__(
        self, in_filters, similarity_dim=128, lookup_window=101, output_dim=128, stop_gradient=False, use_bias=False
    ):
        super().__init__()

        if stop_gradient:
            raise NotImplementedError("停止梯度功能在PyTorch版本中未实现!")

        self.projection = nn.Linear(in_filters, similarity_dim, bias=use_bias)
        self.fc = nn.Linear(lookup_window, output_dim)

        self.lookup_window = lookup_window
        assert lookup_window % 2 == 1, "lookup_window必须是奇数"

    def forward(self, inputs):
        x = torch.cat([torch.mean(x, dim=[3, 4]) for x in inputs], dim=1)
        x = torch.transpose(x, 1, 2)

        x = self.projection(x)
        x = F.normalize(x, p=2, dim=2)

        batch_size, time_window = x.shape[0], x.shape[1]
        similarities = torch.bmm(x, x.transpose(1, 2))
        similarities_padded = F.pad(similarities, [(self.lookup_window - 1) // 2, (self.lookup_window - 1) // 2])

        batch_indices = (
            torch.arange(0, batch_size, device=x.device)
            .view([batch_size, 1, 1])
            .repeat([1, time_window, self.lookup_window])
        )
        time_indices = (
            torch.arange(0, time_window, device=x.device)
            .view([1, time_window, 1])
            .repeat([batch_size, 1, self.lookup_window])
        )
        lookup_indices = (
            torch.arange(0, self.lookup_window, device=x.device)
            .view([1, 1, self.lookup_window])
            .repeat([batch_size, time_window, 1])
            + time_indices
        )

        similarities = similarities_padded[batch_indices, time_indices, lookup_indices]
        return F.relu(self.fc(similarities))


class ColorHistograms(nn.Module):
    """颜色直方图计算层"""

    def __init__(self, lookup_window=101, output_dim=None):
        super().__init__()

        self.fc = nn.Linear(lookup_window, output_dim) if output_dim is not None else None
        self.lookup_window = lookup_window
        assert lookup_window % 2 == 1, "lookup_window必须是奇数"

    @staticmethod
    def compute_color_histograms(frames):
        """计算颜色直方图"""
        frames = frames.int()

        def get_bin(frames):
            R, G, B = frames[:, :, 0], frames[:, :, 1], frames[:, :, 2]
            R, G, B = R >> 5, G >> 5, B >> 5
            return (R << 6) + (G << 3) + B

        batch_size, time_window, height, width, no_channels = frames.shape
        assert no_channels == 3
        frames_flatten = frames.view(batch_size * time_window, height * width, 3)

        binned_values = get_bin(frames_flatten)
        frame_bin_prefix = (torch.arange(0, batch_size * time_window, device=frames.device) << 9).view(-1, 1)
        binned_values = (binned_values + frame_bin_prefix).view(-1)

        histograms = torch.zeros(batch_size * time_window * 512, dtype=torch.int32, device=frames.device)
        histograms.scatter_add_(
            0, binned_values, torch.ones(len(binned_values), dtype=torch.int32, device=frames.device)
        )

        histograms = histograms.view(batch_size, time_window, 512).float()
        histograms_normalized = F.normalize(histograms, p=2, dim=2)
        return histograms_normalized

    def forward(self, inputs):
        x = self.compute_color_histograms(inputs)

        batch_size, time_window = x.shape[0], x.shape[1]
        similarities = torch.bmm(x, x.transpose(1, 2))
        similarities_padded = F.pad(similarities, [(self.lookup_window - 1) // 2, (self.lookup_window - 1) // 2])

        batch_indices = (
            torch.arange(0, batch_size, device=x.device)
            .view([batch_size, 1, 1])
            .repeat([1, time_window, self.lookup_window])
        )
        time_indices = (
            torch.arange(0, time_window, device=x.device)
            .view([1, time_window, 1])
            .repeat([batch_size, 1, self.lookup_window])
        )
        lookup_indices = (
            torch.arange(0, self.lookup_window, device=x.device)
            .view([1, 1, self.lookup_window])
            .repeat([batch_size, time_window, 1])
            + time_indices
        )

        similarities = similarities_padded[batch_indices, time_indices, lookup_indices]

        if self.fc is not None:
            return F.relu(self.fc(similarities))
        return similarities


class TransNetV2Model:
    """
    TransNetV2模型封装类

    提供简单易用的转场检测接口
    """

    def __init__(self, model_path: str | None = None):
        """
        初始化TransNetV2模型

        Args:
            model_path: 模型权重文件路径
        """
        torch.set_num_threads(4)

        self.model = TransNetV2()

        # 加载模型权重
        if model_path is None:
            model_path = str(Path(__file__).resolve().parents[1] / "weights" / "transnetv2-pytorch-weights.pth")

        if not Path(model_path).is_file():
            raise FileNotFoundError(f"TransNetV2 权重不存在: {model_path}")
        try:
            state_dict = torch.load(model_path, map_location="cpu", weights_only=True)
            self.model.load_state_dict(state_dict)
        except Exception as exc:
            raise RuntimeError(f"TransNetV2 权重加载失败: {exc}") from exc

        self.model.eval()

    def predict_video(self, video_path: str, totalframe: int) -> tuple[np.ndarray, list[float]]:
        """
        预测视频的转场点

        Args:
            video_path: 视频文件路径

        Returns:
            Tuple[np.ndarray, List[float]]: (转场场景, 转场分数列表)
        """
        completed = subprocess.run(
            [
                media.ffmpeg_bin(),
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                video_path,
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "-s",
                "48x27",
                "-vframes",
                str(totalframe),
                "pipe:1",
            ],
            capture_output=True,
            check=True,
            timeout=180,
        )
        video = np.frombuffer(completed.stdout, np.uint8).reshape([-1, 27, 48, 3])
        if not len(video):
            raise RuntimeError("TransNetV2 未读取到视频帧")
        _, single_frame_pred, _ = self._predict_raw(video)
        scenes = self._predictions_to_scenes(single_frame_pred)
        return scenes, single_frame_pred.tolist()

    def _predict_raw(self, video: np.ndarray, device=torch.device("cpu")):
        """
        原始预测方法

        Args:
            video: 视频帧数组
            device: 计算设备

        Returns:
            预测结果
        """
        self.model.to(device)
        with torch.no_grad():
            predictions = []
            for inp in self._input_iterator(video):
                video_tensor = torch.from_numpy(inp).to(device)

                single_frame_pred, all_frame_pred = self.model(video_tensor)

                single_frame_pred = torch.sigmoid(single_frame_pred).cpu().numpy()
                all_frame_pred = torch.sigmoid(all_frame_pred["many_hot"]).cpu().numpy()

                predictions.append((single_frame_pred[0, 25:75, 0], all_frame_pred[0, 25:75, 0]))

            single_frame_pred = np.concatenate([single_ for single_, all_ in predictions])
            all_frames_pred = np.concatenate([all_ for single_, all_ in predictions])

            return video.shape[0], single_frame_pred[: len(video)], all_frames_pred[: len(video)]

    def _input_iterator(self, frames):
        """输入迭代器"""
        no_padded_frames_start = 25
        no_padded_frames_end = 25 + 50 - (len(frames) % 50 if len(frames) % 50 != 0 else 50)

        start_frame = np.expand_dims(frames[0], 0)
        end_frame = np.expand_dims(frames[-1], 0)
        padded_inputs = np.concatenate(
            [start_frame] * no_padded_frames_start + [frames] + [end_frame] * no_padded_frames_end, 0
        )

        ptr = 0
        while ptr + 100 <= len(padded_inputs):
            out = padded_inputs[ptr : ptr + 100]
            ptr += 50
            yield out[np.newaxis]

    def _predictions_to_scenes(self, predictions: np.ndarray, threshold: float = 0.5):
        """将预测结果转换为场景"""
        predictions = (predictions > threshold).astype(np.uint8)
        scenes = []
        t, t_prev, start = -1, 0, 0

        for i, t in enumerate(predictions):
            if t_prev == 1 and t == 0:
                start = i
            if t_prev == 0 and t == 1 and i != 0:
                scenes.append([start, i])
            t_prev = t

        if t == 0:
            scenes.append([start, i])

        # 修复所有预测都是1的情况
        if len(scenes) == 0:
            return np.array([[0, len(predictions) - 1]], dtype=np.int32)

        return np.array(scenes, dtype=np.int32)
