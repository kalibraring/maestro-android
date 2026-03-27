# Pocket-GPT Example Config

This example shows how to aim `maestro-android` at Pocket-GPT without baking Pocket-GPT behavior into the package.

From the Pocket-GPT repo root:

```bash
cp /Users/mkamar/Non_Work/Projects/maestro-android/examples/pocket-gpt/maestro-android.pocket-gpt.yaml .maestro-android.yaml
maestro-android doctor
maestro-android lane smoke
maestro-android scoped --flow tmp/maestro-repro.yaml
```

Most Pocket-GPT lanes are configured as `kind: command` so they can keep reusing the repo’s `devctl` and lifecycle wrappers.
