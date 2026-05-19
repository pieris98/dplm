#!/usr/bin/env python
"""Basic test script for DPLM2-Bit model."""

import torch
from byprot.models.dplm2 import DPLM2Bit
from generate_dplm2 import initialize_generation

def test_dplm2_bit_load():
    """Test if DPLM2-Bit model can be loaded from pretrained checkpoint."""
    print("Testing DPLM2-Bit model loading (650M)...")
    try:
        model = DPLM2Bit.from_pretrained("airkingbd/dplm2_bit_650m")
        print(f"✓ Model loaded successfully: {type(model).__name__}")
        print(f"  Device: {next(model.parameters()).device}")
        return model
    except Exception as e:
        print(f"✗ Failed to load model: {e}")
        import traceback
        traceback.print_exc()
        return None

def test_dplm2_bit_generation(model):
    """Test if DPLM2-Bit can generate sequences."""
    print("\nTesting DPLM2-Bit generation...")
    try:
        model = model.cuda() if torch.cuda.is_available() else model
        model.eval()

        # DPLM2-Bit uses same initialization as DPLM2
        input_tokens = initialize_generation(
            task="co_generation",
            length=50,  # shorter for faster testing
            num_seqs=2,
            tokenizer=model.tokenizer,
            device=next(model.parameters()).device
        )[0]

        print(f"  Input shape: {input_tokens.shape}")

        with torch.no_grad():
            samples = model.generate(
                input_tokens=input_tokens,
                max_iter=50,  # fewer iterations for testing
            )

        print(f"✓ Generation successful!")
        print(f"  Output type: {type(samples)}")
        
        if isinstance(samples, dict):
            for k, v in samples.items():
                if hasattr(v, 'shape'):
                    print(f"  {k} shape: {v.shape}")
            
            # Decode sequences
            if 'sequences' in samples:
                decoded = model.tokenizer.batch_decode(samples['sequences'], skip_special_tokens=True)
            else:
                # Find the sequence tensor
                for k, v in samples.items():
                    if hasattr(v, 'shape') and len(v.shape) == 2:
                        decoded = model.tokenizer.batch_decode(v, skip_special_tokens=True)
                        break
        else:
            decoded = model.tokenizer.batch_decode(samples, skip_special_tokens=True)

        if decoded:
            print(f"  Sample output (first 50 chars): {decoded[0][:50]}...")
        return True
    except Exception as e:
        print(f"✗ Generation failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("=" * 60)
    print("DPLM2-Bit Basic Functionality Test")
    print("=" * 60)

    model = test_dplm2_bit_load()
    if model is not None:
        test_dplm2_bit_generation(model)

    print("\n" + "=" * 60)
    print("Test completed!")
    print("=" * 60)
