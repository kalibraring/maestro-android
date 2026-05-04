# Pocket-GPT Example Config

This example shows how to aim `maestro-android` at Pocket-GPT without baking Pocket-GPT behavior into the package.

From the Pocket-GPT repo root:

```bash
cp /path/to/maestro-android/examples/pocket-gpt/maestro-android.pocket-gpt.yaml .maestro-android.yaml
maestro-android doctor
maestro-android devices --json
maestro-android start-device
maestro-android lane smoke --device emulator-5554
maestro-android device probe --device <serial>
maestro-android cloud probe --flow tests/maestro-cloud/scenario-runtime-ready-smoke.yaml
```

Most Pocket-GPT lanes are configured as `kind: command` so they can keep reusing the repo’s `devctl` and lifecycle wrappers.

Suggested Pocket-GPT flow:

1. emulator for the fast bootstrap/runtime check
2. one connected phone for real transport/OEM proof
3. cloud probe or cloud smoke for hosted confirmation
