# Region Chest Inspector

Read-only Python 3.10+ tool based on the chest reader used by the Elseborn recipe importer. Reads a zlib-compressed Anvil chunk and prints chest records as JSON. Coordinates refer to the chunk within the region (0–31). Supports the standard in-region zlib format.

```sh
python inspect_chests.py r.0.0.mca 0 0
```
