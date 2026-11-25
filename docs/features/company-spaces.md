# Company files and coworking spaces

A **coworking space** is shared storage owned by the **company**, not by a
project. It is how a company keeps its own files next to the files of every
project it runs, without copying anything.

A space lives inside the company document (`Company.spaces`) and its physical
files live in the company folder:

```text
<companies_root>/<company_id>/
├── company.json                  # the Company document (spaces included)
├── journal.jsonl                 # audit trail (space.created, space.updated…)
└── spaces/<space_id>/files/      # the company's own files of that space
```

Because the space belongs to the company, renaming or unlinking a project never
moves the company files, and deleting a company deletes one folder. The space
itself is stored with the company, so it follows the company across every
project screen.

## Virtual folders

The file browser is **virtual**: it lists the company files and, below two
reserved roots, the real folders of the company projects and of any host folder
the owner mapped:

```text
/                          company files + the virtual roots
├── projects/              one folder per mapped company project
│   └── <project_id>/…     the project's real files (read only)
├── folders/               one folder per mapped host folder
│   └── <folder_id>/…      the host folder's real files (read only by default)
└── <company files>/…      writable, stored under the company
```

Nothing is copied. Every navigation resolves the project or host folder on the
server, so the browser always shows the current disk state and each project
keeps a single source of truth. Writes are only accepted inside the company's
own files folder; mapped projects/folders are read only through the company
space (a mapped folder can opt into writes).

## Model

```json
{
  "spaces": [{
    "id": "spc-…",
    "name": "Company files",
    "description": "Shared files of the company",
    "project_ids": ["proj_123"],
    "folders": [{
      "id": "fld-…",
      "name": "Contracts",
      "path": "/srv/drives/contracts",
      "readonly": true
    }],
    "icon": "fa-solid fa-folder-tree"
  }]
}
```

* `project_ids` must reference projects the company can already see (linked, or
  visible through `all_projects`). The project files are never duplicated.
* `folders` are absolute paths on the codx-junior host. The validation rejects
  relative paths, the filesystem root, `/proc`, `/sys`, `/dev`, `/run` and the
  company's own internal folder.
* A company that predates spaces gets a default **Company files** space on the
  first request, mapping every linked project.

## REST API

All endpoints are company-scoped: they do not require a project session.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/companies/{id}/spaces` | List the spaces (creates the default one when missing). |
| `POST` | `/api/companies/{id}/spaces` | Create a space. |
| `GET` | `/api/companies/{id}/spaces/{space}` | One space with mapped projects/folders. |
| `PUT` | `/api/companies/{id}/spaces/{space}` | Update name, mappings or presentation. |
| `DELETE` | `/api/companies/{id}/spaces/{space}` | Delete the space (files move to the company trash). |
| `GET` | `/api/companies/{id}/spaces/{space}/files?path=` | List the virtual browser at `path`. |
| `GET` | `/api/companies/{id}/spaces/{space}/file?path=` | Stream a file inline (`download=true` for an attachment). |
| `POST` | `/api/companies/{id}/spaces/{space}/folders` | Create a company folder. |
| `POST` | `/api/companies/{id}/spaces/{space}/upload` | Upload a file into the company files. |
| `PUT` | `/api/companies/{id}/spaces/{space}/file` | Create/replace a text file. |
| `DELETE` | `/api/companies/{id}/spaces/{space}/file?path=` | Delete a company file or an empty folder. |

Reads are allowed to every company member; writes require the company owner,
admin or manager, exactly like the rest of the company APIs.

## UI

The company shell gets a **Files** section (`CompanyFiles.vue`). It shows the
spaces rail, the browser with the `Projects` and `Folders` virtual roots, a file
preview (text, images, PDFs), and — for company admins/managers — new folders,
uploads, deletion and the **Space settings** dialog where projects and host
folders are mapped.

## Security notes

* Every path is normalized and resolved with `realpath`; `..` and paths that
  escape their root are rejected.
* Only the company files folder is writable through a space. Project files and
  mapped folders are read only unless the mapped folder explicitly allows
  writes.
* A non-empty company folder cannot be deleted (only files or empty folders),
  and deleting a space moves its folder to `<company>/spaces/.trash/` rather
  than erasing it.

## Tests

```bash
cd api
pytest tests/test_company_spaces.py -q
```

The suite covers the stored company document, company-file CRUD, the virtual
project/mapped-folder navigation, write restrictions, path traversal, the
snapshot and the HTTP contract of the browser endpoints.
