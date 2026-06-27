import os
import subprocess


def load_keepass_env(db_path, entry_path, master_password):
    """Extracts a protected password from KeePassXC and sets it as an environment variable."""
    # Build the exact command requiring the -a and -s flags
    cmd = ["keepassxc-cli", "show", db_path, entry_path, "-a", "password", "-s"]

    try:
        # Run command, piping the master password to stdin
        result = subprocess.run(
            cmd,
            input=master_password,
            text=True,
            capture_output=True,
            check=True,
        )

        # Clean trailing newlines from the output
        secret_value = result.stdout.strip()

        # Load directly into Python's environment variables
        os.environ[entry_path] = secret_value
        print(f"✅ Successfully loaded {entry_path} into environment.")

    except subprocess.CalledProcessError as e:
        print(f"❌ Failed to read database: {e.stderr.strip()}")
        raise
