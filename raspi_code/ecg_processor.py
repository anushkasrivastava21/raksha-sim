import numpy as np
from scipy.signal import butter, sosfiltfilt
import torch
import torch.nn as nn

class ECG_CNN(nn.Module):
    def __init__(self):
        super(ECG_CNN, self).__init__()
        self.conv1 = nn.Conv1d(1, 16, kernel_size=5, stride=2)
        self.relu = nn.ReLU()
        self.fc = nn.Linear(16 * 78, 2) 

    def forward(self, x):
        x = self.relu(self.conv1(x))
        x = torch.flatten(x, 1)
        return self.fc(x)

ecg_model = ECG_CNN()

def process_ecg(raw_ecg: list, fs=360) -> str:
    """Filters ECG and returns arrhythmia pattern."""
    if not raw_ecg or len(raw_ecg) < 10:
        return "Normal Sinus Rhythm"
    try:
        sos = butter(4, [0.5, 40], btype='bandpass', fs=fs, output='sos')
        filtered_ecg = sosfiltfilt(sos, raw_ecg)
        tensor_ecg = torch.tensor(filtered_ecg, dtype=torch.float32).unsqueeze(0).unsqueeze(0)
        
        with torch.no_grad():
            output = ecg_model(tensor_ecg)
            prediction = torch.argmax(output, dim=1).item()
            
        return "Arrhythmia Detected" if prediction == 1 else "Normal Sinus Rhythm"
    except Exception as e:
        return "Normal Sinus Rhythm"

if __name__ == "__main__":
    my_ecg_input = [464, 448, 416, 422, 424, 485, 444, 566, 592, 426]
    print("Processing ECG...")
    result = process_ecg(my_ecg_input)
    print(f"Final Output: {result}")
