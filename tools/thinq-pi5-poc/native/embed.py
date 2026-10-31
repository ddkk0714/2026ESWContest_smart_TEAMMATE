import json
import pathlib
import sys

root = pathlib.Path(__file__).resolve().parents[1]
mods = {}
for name, path in [
    ("tools.thinq_connect.probe", "tools/thinq_connect/probe.py"),
    ("tools.thinq_connect.manual_trial", "tools/thinq_connect/manual_trial.py"),
    ("tools.thinq_connect.air_quality", "tools/thinq_connect/air_quality.py"),
    ("poc.session", "poc/session.py"),
    ("poc.worker", "poc/worker.py"),
]:
    mods[name] = (root / path).read_text()
bootstrap = "import sys,types,json\n"
bootstrap += "for n in ['tools','tools.thinq_connect','poc']:\n m=types.ModuleType(n);m.__path__=[];sys.modules[n]=m\n"
bootstrap += "sources=" + repr(mods) + "\n"
bootstrap += "for n,s in sources.items():\n m=types.ModuleType(n);m.__file__='<embedded>';sys.modules[n]=m;exec(compile(s,'<embedded>','exec'),m.__dict__); parent,leaf=n.rsplit('.',1);setattr(sys.modules[parent],leaf,m)\n"
bootstrap += (
    "sys.modules['poc.worker'].main("
    + repr(json.loads((root / "poc/fixture.json").read_text()))
    + ")\n"
)
pathlib.Path(sys.argv[1]).write_text(
    "#pragma once\ninline const char kWorker[] = " + json.dumps(bootstrap) + ";\n"
)
