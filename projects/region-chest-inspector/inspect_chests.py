import struct, zlib, json
from pathlib import Path
class NBT:
 def __init__(self,b): self.b=b;self.p=0
 def take(self,n): b=self.b[self.p:self.p+n];self.p+=n;return b
 def num(self,f): return struct.unpack('>'+f,self.take(struct.calcsize(f)))[0]
 def string(self):
  raw=self.take(self.num('H')).replace(b'\xc0\x80',b'\x00')
  return raw.decode('utf8',errors='surrogatepass').encode('utf-16-le',errors='surrogatepass').decode('utf-16-le',errors='surrogatepass')
 def value(self,t):
  if t in (1,2,3,4,5,6): return self.num({1:'b',2:'h',3:'i',4:'q',5:'f',6:'d'}[t])
  if t==8:return self.string()
  if t==9:
   k=self.num('B');return [self.value(k) for _ in range(self.num('i'))]
  if t==10:
   d={}
   while (k:=self.num('B')): name=self.string();d[name]=self.value(k)
   return d
  if t in (7,11,12):
   n=self.num('i');return [self.num({7:'b',11:'i',12:'q'}[t]) for _ in range(n)]
  raise ValueError(t)
def chunks(path,coords=None):
 b=path.read_bytes()
 if coords is None:coords=[(cx,cz) for cz in (0,1) for cx in (0,1)]
 for cx,cz in coords:
   i=4*(cx+32*cz);off=int.from_bytes(b[i:i+3],'big')*4096
   if not off:continue
   n=int.from_bytes(b[off:off+4],'big');kind=b[off+4];raw=b[off+5:off+4+n]
   assert kind==2,kind
   r=NBT(zlib.decompress(raw));t=r.num('B');r.string();yield r.value(t)

def main():
    import argparse

    parser = argparse.ArgumentParser(description='Inspect chests in an Anvil chunk')
    parser.add_argument('region', type=Path)
    parser.add_argument('chunk_x', type=int, choices=range(32), metavar='0..31')
    parser.add_argument('chunk_z', type=int, choices=range(32), metavar='0..31')
    args = parser.parse_args()
    records = [
        entry
        for chunk in chunks(args.region, [(args.chunk_x, args.chunk_z)])
        for entry in chunk.get('block_entities', [])
        if entry.get('id') in {'minecraft:chest', 'minecraft:trapped_chest'}
    ]
    print(json.dumps(records, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
