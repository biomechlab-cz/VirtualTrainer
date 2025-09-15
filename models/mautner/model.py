import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np


class Net(nn.Module):
    def __init__(self, in_channels=20, seq_len=200):
        super(Net, self).__init__()
        cnn1 = 64
        cnn2 = 128
        cnn3 = 128
        self.cnn1 = nn.Conv1d(in_channels, cnn1, kernel_size=3, padding=1)
        self.normalization1 = nn.BatchNorm1d(cnn1)
        self.pool1 = nn.MaxPool1d(2)
        self.cnn2 = nn.Conv1d(cnn1, cnn2, kernel_size=3, padding=1)
        self.normalization2 = nn.BatchNorm1d(cnn2)
        self.pool2 = nn.MaxPool1d(2)
        self.cnn3 = nn.Conv1d(cnn2, cnn3, kernel_size=3, padding=1)
        self.normalization3 = nn.BatchNorm1d(cnn3)
        self.pool3 = nn.MaxPool1d(2)
        self.flatten = nn.Flatten()

        with torch.no_grad():
            dummy = torch.zeros(1, in_channels, seq_len)
            out = self.pool1(F.relu(self.cnn1(dummy)))
            out = self.pool2(F.relu(self.cnn2(out)))
            out = self.pool3(F.relu(self.cnn3(out)))
            flat_dim = out.view(1, -1).size(1)

        self.snn1 = nn.Linear(flat_dim, 64)
        self.dropout1 = nn.Dropout(p=0.5)
        self.snn2 = nn.Linear(64, 8)
        self.dropout2 = nn.Dropout(p=0.3)
        self.snn3 = nn.Linear(8, 2)

    def forward(self, x):
        x = self.cnn1(x)
        x = F.relu(x)
        x = self.normalization1(x)
        x = self.pool1(x)
        x = self.cnn2(x)
        x = F.relu(x)
        x = self.normalization2(x)
        x = self.pool2(x)
        x = self.cnn3(x)
        x = F.relu(x)
        x = self.normalization3(x)
        x = self.pool3(x)
        x = self.flatten(x)
        x = self.snn1(x)
        x = F.relu(x)
        x = self.dropout1(x)
        x = self.snn2(x)
        x = F.relu(x)
        x = self.dropout2(x)
        out = self.snn3(x)
        return out


class ModelInterface:
    def __init__(self, model_path=r"models/mautner/cnn_model_3.pth"):
        self.device = "cpu"
        self.model = Net().to(device=self.device)
        self.model.load_state_dict(torch.load(model_path, map_location=self.device))
        self.log_mask = [0, 5, 10, 15]
        self.model.eval()
        self.target_shape = (1, 20, 200)  # [sample, kanaly, okno]

    def predict(self, sample):
        """
        :param sample: musi mit shape (1,20,200) kde 20 je pocet vektoru a 200 je pocet prvku v jednom vektoru
        kanaly musi odpovidat:
                                "Biceps_EMG_Envelope", "Biceps_Q1", "Biceps_Q2", "Biceps_Q3", "Biceps_Q4",
                            "Triceps_EMG_Envelope", "Triceps_Q1", "Triceps_Q2", "Triceps_Q3", "Triceps_Q4",
                            "Gastrocnemious_EMG_Envelope","Gastrocnemious_Q1","Gastrocnemious_Q2","Gastrocnemious_Q3","Gastrocnemious_Q4",
                            "Rectus_EMG_Envelope","Rectus_Q1","Rectus_Q2","Rectus_Q3","Rectus_Q4" v tomto poradi
        :return: vraci predikci fáze
        """

        sample = torch.tensor(sample, dtype=torch.float32).to(device=self.device)

        if sample.shape != self.target_shape:
            if sample.shape[1] * sample.shape[2] == self.target_shape[1] * self.target_shape[2]:
                sample = sample.permute(0, 2, 1)
            else:
                raise Exception("Sample must have shape (1, 20, 200)")

        sample[:, self.log_mask, :] = torch.log(torch.abs(sample[:, self.log_mask, :]))
        sample = self.normalize(sample)

        with torch.no_grad():
            output = self.model(sample)
            output_np = output.detach().cpu().numpy()
            output_atan = np.arctan2(output_np[:, 0], output_np[:, 1]) / (2 * np.pi)
            out = ((output_atan + 1) % 1.0) * 100
            return out

    def normalize(self, signal):
        mean = signal.mean(dim=2, keepdim=True)
        std = signal.std(dim=2, keepdim=True)
        return (signal - mean) / std
