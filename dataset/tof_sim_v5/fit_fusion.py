"""두 센서 logits 를 합치는 작은 선형 융합을 맞춰 보고, 단순 규칙(softmax 평균 등)과 세션 단위 교차검증으로 비교한다.

    python fit_fusion.py runs/pair_random48.npz            # eval_pair.py --dump 결과
    python fit_fusion.py runs/pair_random48.npz --save runs/fusion.npz

입력 특징 = [log_softmax(l0), log_softmax(l1)] (2C), 출력 = C 클래스 logits. 세션을 K 겹으로 나눠 학습·평가하고
겹 평균을 보고한다. 이기면 export.py 가 posture_net.bin 뒤에 붙일 수 있는 크기(2C×C + C)다.
"""

from __future__ import annotations

import argparse

import numpy as np
import torch


def log_softmax(z):
    z = z - z.max(-1, keepdims=True)
    return z - np.log(np.exp(z).sum(-1, keepdims=True))


def macro_f1(pred, y, C):
    cm = np.zeros((C, C), dtype=np.int64)
    np.add.at(cm, (y, pred), 1)
    tp = np.diag(cm).astype(float)
    r, p = tp / np.maximum(cm.sum(1), 1), tp / np.maximum(cm.sum(0), 1)
    f = 2 * r * p / np.maximum(r + p, 1e-9)
    present = cm.sum(1) > 0
    return float(f[present].mean()), float(tp.sum() / max(cm.sum(), 1))


def fit_linear(X, y, C, epochs=300, lr=0.05, wd=1e-4, seed=0):
    torch.manual_seed(seed)
    Xt, yt = torch.from_numpy(X).float(), torch.from_numpy(y).long()
    lin = torch.nn.Linear(X.shape[1], C)
    with torch.no_grad():                                   # softmax 평균과 비슷한 자리에서 출발
        lin.weight.zero_(); lin.bias.zero_()
        lin.weight[:, :C] = torch.eye(C); lin.weight[:, C:] = torch.eye(C)
    # 클래스 불균형: 드문 클래스에 가중
    w = torch.bincount(yt, minlength=C).float()
    w = (w.sum() / torch.clamp(w, min=1)) ** 0.5
    opt = torch.optim.Adam(lin.parameters(), lr=lr, weight_decay=wd)
    for _ in range(epochs):
        opt.zero_grad()
        loss = torch.nn.functional.cross_entropy(lin(Xt), yt, weight=w)
        loss.backward()
        opt.step()
    return lin.weight.detach().numpy().astype(np.float32), lin.bias.detach().numpy().astype(np.float32)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dump")
    ap.add_argument("--folds", type=int, default=4)
    ap.add_argument("--save", default=None, help="전체 데이터로 맞춘 W(C×2C), b(C) 를 npz 로")
    args = ap.parse_args()

    d = np.load(args.dump, allow_pickle=False)
    l0, l1, y, keep, sess = d["l0"], d["l1"], d["label"].astype(np.int64), d["keep"], d["session"].astype(np.int64)
    labels = [str(s) for s in d["labels"]]
    C = len(labels)
    lp0, lp1 = log_softmax(l0), log_softmax(l1)
    p0, p1 = np.exp(lp0), np.exp(lp1)
    X = np.concatenate([lp0, lp1], axis=1)
    n_sess = int(sess.max()) + 1
    print(f"{n_sess} sessions, {int(keep.sum()):,} scored frames, {C} classes")

    rules = {
        "left": lambda m: l0[m].argmax(-1), "right": lambda m: l1[m].argmax(-1),
        "mean_prob": lambda m: (p0[m] + p1[m]).argmax(-1),
        "sum_logprob": lambda m: (lp0[m] + lp1[m]).argmax(-1),
        "max_conf": lambda m: np.where(p0[m].max(-1) >= p1[m].max(-1), l0[m].argmax(-1), l1[m].argmax(-1)),
    }
    totals = {k: [] for k in list(rules) + ["linear"]}
    rng = np.random.default_rng(0)
    order = rng.permutation(n_sess)
    folds = np.array_split(order, args.folds)
    for k, test_sess in enumerate(folds):
        test = keep & np.isin(sess, test_sess)
        train = keep & ~np.isin(sess, test_sess)
        W, b = fit_linear(X[train], y[train], C)
        for name, fn in rules.items():
            totals[name].append(macro_f1(fn(test), y[test], C))
        pred = (X[test] @ W.T + b).argmax(-1)
        totals["linear"].append(macro_f1(pred, y[test], C))
        print(f"fold {k}: " + " ".join(f"{n}={v[-1][1]:.3f}/{v[-1][0]:.3f}" for n, v in totals.items()))
    print()
    print(f"{'rule':12s} {'acc':>6s} {'macroF1':>8s}   (세션 {args.folds}-겹 평균, 테스트 겹만)")
    for name, v in totals.items():
        f1s, accs = zip(*v)
        print(f"{name:12s} {np.mean(accs):6.3f} {np.mean(f1s):8.3f}")

    if args.save:
        W, b = fit_linear(X[keep], y[keep], C)
        np.savez(args.save, W=W, b=b, labels=np.array(labels), features="log_softmax(l0) ++ log_softmax(l1)")
        print(f"-> {args.save}  W {W.shape} b {b.shape}")


if __name__ == "__main__":
    main()
