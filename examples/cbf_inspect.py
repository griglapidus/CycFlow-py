"""
Walk a .cbf file section by section using the low-level CbfFile API.

Useful for debugging, extracting metadata, or implementing custom readers
that don't fit the CbfReader streaming model.
"""
import sys
import cyclib


def main(path: str) -> None:
    with cyclib.CbfFile() as f:
        if not f.open(path, cyclib.CbfMode.Read):
            raise SystemExit(f"Cannot open {path}")

        section_index = 0
        while True:
            header = f.read_section_header()
            if header is None:
                break

            print(f"[{section_index}] {header!r}")

            if header.type == int(cyclib.CbfSectionType.Header):
                rule = f.read_rule(header)
                if rule is not None:
                    print(f"    Schema: {len(rule)} fields, {rule.get_rec_size()} bytes/record")
                    for attr in rule:
                        print(f"      - {attr!r}")
            elif header.type == int(cyclib.CbfSectionType.Data):
                print(f"    Data: {header.body_length} bytes")
                f.skip_section(header)
            else:
                print(f"    Unknown section type, skipping")
                f.skip_section(header)

            section_index += 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python cbf_inspect.py <path/to/file.cbf>")
        raise SystemExit(1)
    main(sys.argv[1])
