docker run --rm --gpus all --network none \
  -v "$PWD/docker-smoke-output:/runs" \
  dplm:cu121-torch220 \
  bash -lc '
python - <<PY
import torch
import esm
from pathlib import Path

seq = "MKTAYIAKQRQISFVKSHFSRQ"

model = esm.pretrained.esmfold_v1().eval().cuda()
model.set_chunk_size(64)

with torch.no_grad():
    pdb = model.infer_pdb(seq)

out = Path("/runs/esmfold_smoke.pdb")
out.write_text(pdb)

lines = pdb.splitlines()
atom_lines = [l for l in lines if l.startswith(("ATOM", "HETATM"))]
plddt_vals = [float(l[60:66]) for l in atom_lines if len(l) >= 66]

print("sequence:", seq)
print("pdb_lines:", len(lines))
print("atom_lines:", len(atom_lines))
print("mean_plddt:", round(sum(plddt_vals) / len(plddt_vals), 2) if plddt_vals else "n/a")
print("first_atom:", atom_lines[0] if atom_lines else "none")
print("saved:", out)
PY
'
