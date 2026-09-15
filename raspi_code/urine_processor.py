import torch
import torch.nn as nn

class Urine_CNN(nn.Module):
    def __init__(self):
        super(Urine_CNN, self).__init__()
        self.net = nn.Sequential(
            nn.Linear(3, 16),
            nn.ReLU(),
            nn.Linear(16, 2) 
        )

    def forward(self, x):
        return self.net(x)

urine_model = Urine_CNN()

def process_urine(urine_rgb: list) -> int:
    """Consumes [R, G, B] and outputs severity class (0 or 1)."""
    if not urine_rgb or len(urine_rgb) < 3:
        return 0
    tensor_rgb = torch.tensor(urine_rgb, dtype=torch.float32)
    
    with torch.no_grad():
        output = urine_model(tensor_rgb)
        severity_class = torch.argmax(output).item()
        
    return severity_class
