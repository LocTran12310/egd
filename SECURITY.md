# Security

Please report vulnerabilities privately through GitHub's "Report a vulnerability"
(Security tab) rather than in a public issue.

Areas that matter most:
- secret redaction in proof transcripts (`src/egd/proof/`)
- `egd serve` file serving and token check (`src/egd/dashboard.py`)
- the console (`src/egd/console.py`): write actions signed as the console user, the CSRF
  header and Origin check, the Host check and token, folder browsing confined to the browse
  roots, and "Set up EGD", which writes `.egd/` into a folder
- anything that executes commands from `proof.toml` or `config.toml`

`proof.toml` and `config.toml` run commands with your shell — treat them like any other
script in your repository and review changes to them.
