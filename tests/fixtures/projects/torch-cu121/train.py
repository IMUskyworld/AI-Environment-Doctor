"""Tiny CIFAR-style training entry point (fixture)."""

import torch


def main() -> None:
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = torch.nn.Linear(32, 4).to(device)
    batch = torch.randn(8, 32, device=device)
    loss = model(batch).sum()
    loss.backward()
    print("ok", device)


if __name__ == "__main__":
    main()
