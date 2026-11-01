# Synthetic testing data

Synthetic testing data has a separate lifecycle from other generated deliverables:

```text
data/
  raw/                         preserved acquired originals
  processed/                   derived working datasets
  generated/                   other generated outputs and deliverables
  synthetic/
    datasets/<dataset-id>/<version>/
      manifest.json            immutable reusable test input and content hashes
      ...
    runs/<run-id>/
      manifest.json            ownership, selected input versions and generation metadata
      intermediate/
      outputs/
```

The approved `synthetic.root` defaults to `data/synthetic`, but can name a separate
local folder under data/. Dataset IDs and versions stay stable when the root
changes. The initial implementation has no remote-storage adapter. Changing the
root does not migrate data; arrange any migration separately and preserve hashes.

## Generate, seal, publish and consume

Use a standard building task with your own live claim. In bounded mode its
approved output locations must contain the requested destinations. Lifecycle
commands do not execute generators, fetch external data, or call models.

```sh
python3 scripts/workflow.py synthetic-run T001 --run example-run \
    --file /tmp/synthetic-metadata.json --disposable
# Run the approved tracked generator, writing only into this run's outputs/.
python3 scripts/workflow.py synthetic-seal T001 --run example-run
python3 scripts/workflow.py synthetic-publish T001 --run example-run \
    --dataset example-cohort --version v1
python3 scripts/workflow.py synthetic-verify --dataset example-cohort --version v1
python3 scripts/workflow.py synthetic-resolve --dataset example-cohort --version v1
python3 scripts/workflow.py synthetic-run T001 --run consumer-run \
    --file /tmp/synthetic-metadata.json --dataset example-cohort@v1
```

Copy templates/synthetic-metadata.json outside the source tree and fill in purpose,
origin, generator revision, parameters, seed, reproducibility limitations,
sensitivity, retention and lineage references. A seed is not a reproducibility
guarantee. Keep secrets and record-level values out of metadata. Generator code
belongs in tracked source covered by task scope and review.

Each run gets a fresh ID and separate directories. Input selection requires exact
`dataset-id@version`; creation verifies inputs and captures their manifest hashes and version references, without recursive copies.
Sealing checks those inputs again and hashes run contents. Publication checks the
sealed run, copies outputs into staging, verifies copied bytes, and creates a new
dataset version. Existing versions are refused. Root manifest.json is reserved.
Use a new version instead of editing a published dataset. Supported edit guards
reject writes under datasets/; ordinary filesystem access is not sandboxed.

`synthetic-resolve` verifies file hashes and returns a JSON path that tests can use.
Configure tests with logical IDs and versions rather than scanning arbitrary files.
Unexpected added, changed or missing dataset files fail verification. Dataset
manifests supply provenance and integrity checks, not signed tamper-proof evidence.

Acquired synthetic test inputs may enter through an approved isolated run and be
published as a versioned dataset. Record their origin and lineage; duplication
under raw/ is required only when the project's preservation policy requires it.
External acquisition still requires approved source and download scope. Synthetic
status and sensitivity are independent: derived material retains applicable
source restrictions. The folder name cannot establish that data is safe.

## Retention, inspection and compatibility

Runs are retained unless explicitly marked disposable. `synthetic-clean TASK
--run ID` removes only the named run when both disposable status and
an approved `synthetic.disposable_cleanup_approval` reference permit it. Otherwise,
separate explicit deletion approval is required via `--approved`. Retained runs
always require that separate approval. Once the original task is finished, a new
standard cleanup task may own the deletion, with its own live claim and approved
output scope. Runs belonging to another active task cannot be removed. A workflow receipt preserves metadata
before deletion. There is no background deletion job or dataset-deletion command.

Data contents stay out of Git by default. Only small explicitly approved files in
`data.approved_fixtures` may be committed. Dataset manifests reside alongside data;
they are not automatically public or tracked. Do not add blanket Git exceptions.

The data-analysis add-on's automatic discovery skips the configured synthetic
area after adoption. Legacy discovery remains unchanged when synthetic settings
are absent. Declare exact synthetic files
in analysis.datasets with explicit pre/post phases when inspection is needed;
select reusable inputs as pre and produced outputs as post. This avoids treating
all synthetic material as input solely because it shares a directory name.

Existing configuration files without synthetic settings keep working; adopt the
section through update-config preview and approval. No existing data is moved,
relabeled, overwritten or deleted automatically. The template creates only the
empty datasets/ and runs/ layout. Partition larger datasets according to measured
access patterns when needed; no domain-specific partition scheme is imposed.
