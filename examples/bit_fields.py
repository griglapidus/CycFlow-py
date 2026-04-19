"""
Working with named bit fields in CycLib.

CycLib supports named bits inside integer fields: declare a PAttr with
bit_defs, then read/write the bits by name. The numpy structured dtype
does not expand individual bits — only the containing integer word.
"""
import cycflow


def main() -> None:
    # 1. Declare the schema. StatusReg is a uint8 with 4 named bits:
    #    tx and rx are bits 0 and 1, then 4 reserved bits, and errFlag
    #    is bit 6.
    rule = cycflow.make_rule([
        ("Counter",   cycflow.DataType.UInt32),
        ("StatusReg", cycflow.DataType.UInt8, ["tx", "rx", "4", "errFlag"]),
    ])

    id_counter = cycflow.PReg.get_id("Counter")
    tx_id      = cycflow.PReg.get_id("tx")
    err_id        = cycflow.PReg.get_id("errFlag")

    # 2. Fill the buffer with one record.
    buf = cycflow.RecBuffer(rule, capacity=100)
    writer = cycflow.RecordWriter(buf, batch_capacity=10)

    rec = writer.next_record()
    rec.set_uint32(id_counter, 42)
    rec.set_bit(tx_id, True)      # set bit 0 of StatusReg
    rec.set_bit(err_id, True)     # set bit 6 of StatusReg
    writer.commit_record()
    writer.flush()

    # 3. A snapshot gives a structured numpy array where the whole byte
    #    StatusReg is one field.
    snap = buf.snapshot()
    print("Fields in dtype:", snap.dtype.names)
    print(f"Counter={snap['Counter'][0]}  StatusReg=0b{snap['StatusReg'][0]:08b}")

    # 4. For bit-level access, use Record together with PReg.
    reader = cycflow.RecordReader(buf, batch_capacity=10)
    rec = reader.next_record()
    if rec.is_valid():
        print("tx:",      rec.get_bit(tx_id))
        print("errFlag:", rec.get_bit(err_id))
    reader.stop()

    # Alternative: mask the byte in numpy directly.
    # tx_mask = (snap["StatusReg"] & (1 << 0)).astype(bool)


if __name__ == "__main__":
    main()
