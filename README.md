# Kadence Oracle VM retry

Tries to launch the Oracle Cloud Always Free ARM VM "Kadence" (2 OCPU / 12 GB / 165 GB, Ubuntu 24.04)
on a schedule using GitHub Actions, so it keeps running when your PC is off.
When the VM is up (or the launch is blocked) it opens an issue (GitHub emails you) and disables itself.

## Setup

1. Push this folder to a GitHub repo (private is safest; see "Minutes" below).
2. Repo -> Settings -> Secrets and variables -> Actions -> New repository secret. Add these 6 secrets
   (values come from your local `~/.oci/config` and `~/.ssh`):

| Secret | Where to find it |
|---|---|
| `OCI_USER` | `user=` line in `~/.oci/config` |
| `OCI_TENANCY` | `tenancy=` line |
| `OCI_FINGERPRINT` | `fingerprint=` line |
| `OCI_REGION` | `region=` line |
| `OCI_KEY_PEM` | the whole contents of the `.pem` file named by `key_file=` |
| `SSH_PUBLIC_KEY` | contents of `~/.ssh/control-hub.pub` |

3. Actions tab -> "Kadence VM retry" -> Run workflow (test run). The last line of the log tells the result.
4. Once it works, disable the local scheduled task so two things do not try at once.

## Minutes

- Public repo: unlimited free Actions minutes, so edit the cron in `.github/workflows/kadence.yml` to `*/10 * * * *`.
- Private repo: 2000 free minutes/month, so keep the default every 30 minutes.

Never commit the `.pem` key or the OCI config. `.gitignore` blocks them.
