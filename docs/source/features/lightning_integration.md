# PyTorch Lightning Integration

`nshconfig` seamlessly integrates with PyTorch Lightning by implementing the `Mapping` interface. This allows you to use your configs directly as the `hparams` argument in your Lightning modules without any additional effort.

## Basic Usage

```python
import nshconfig as C
import pytorch_lightning as pl

class ModelConfig(C.Config):
    hidden_size: int
    num_layers: int
    learning_rate: float

class MyModel(pl.LightningModule):
    def __init__(self, hparams: ModelConfig):
        super().__init__()
        # PyTorch Lightning will automatically save the config
        self.save_hyperparameters(hparams)

        self.model = nn.Sequential(
            *[nn.Linear(hparams.hidden_size, hparams.hidden_size)
              for _ in range(hparams.num_layers)]
        )

    def configure_optimizers(self):
        return torch.optim.Adam(
            self.parameters(),
            lr=self.hparams.learning_rate
        )

# Create your config
config = ModelConfig(
    hidden_size=256,
    num_layers=4,
    learning_rate=0.001
)

# Use it with your model
model = MyModel(hparams=config)
```

## Benefits

1. **Type Safety**: Get full type checking and IDE support for your hyperparameters
2. **Automatic Serialization**: PyTorch Lightning can automatically save and load your configurations
3. **Clean Interface**: No need for dictionary conversions or special handling
4. **Validation**: All the validation features of `nshconfig` work with Lightning's hyperparameter system
