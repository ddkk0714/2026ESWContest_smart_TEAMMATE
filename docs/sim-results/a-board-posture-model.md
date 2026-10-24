# (a) 보드 자세 모델 결정 — opt_l8_c256 best (10-22 ~ 10-24)

> **[실측]** = 10-19 녹화 1개(정면 80 cm, `real_20261019_p1_front80cm_before17.npz`, 17단계 전까지). 실측은 이것뿐이다.
> **[시뮬]** = tofsim MuJoCo 합성 격자. 시뮬 순위와 실측 순위가 엇갈려 **실측을 따른다.**

## 결론

- Pi 4 자세 서비스 기본 모델은 **opt_l8_c256 best**(v4 계열, step 3708)로 둔다. 1022 번들의 기본은 r9_lowlr 이고 opt_l8_c256 은 후보 폴더 `models/opt_l8_c256` 이다(보드에서는 `TOFPOSTURE_MODEL=models/opt_l8_c256/posture_net.bin`).
- 10-23~24 보강 학습 16개(자세 보강 rebal 8 · 약한 끄덕임 dz 6 · 기준 포함)를 실측으로 채점했는데 **하나도 opt_l8_c256 을 넘지 못했다.**
- **[실측]** 모든 모델의 drowsy 재현율이 0.16, face_down 은 1.0 → 시연 졸음은 **엎드려 졸기 또는 큰 끄덕임**으로 한다.

## 실측 채점 — 두 센서 합침 [실측]

출처: `handoff_laptop_20261024/pi4_bundle_inputs/README.md`(PC 총괄, `real_from_record.py` md5 da836a55, 두 센서 log-prob 합, 같은 구간끼리 짝). 표 전체: [`data/real_1019_fused_scores.csv`](data/real_1019_fused_scores.csv).

| 모델 | acc | macro F1 | 비고 |
|---|---|---|---|
| **opt_l8_c256 best** | **0.365** | **0.456** | 권고 |
| v5_ft_last | 0.279 | 0.374 | |
| dz_v5_weak_last | 0.258 | 0.348 | 약한 끄덕임 보강 중 최고 |
| rb_ftv4_best | 0.238 | 0.391 | |
| rb_ft_mis07_last | 0.194 | 0.249 | 시뮬 자세 한 바퀴 1위(0.846) |
| opt_l8v5_c256 best (v7b, 시뮬 기본) | 0.147 | 0.219 | |
| dz_v5_weak_aim_last | 0.100 | 0.150 | |
| r9_lowlr (1022 번들 기본) | 0.140 | 0.227 | 노트북 채점기와 일치 확인 |

### 같은 녹화, 센서 하나씩 채점 [실측] — 다른 방식이라 숫자가 다르다

출처: 계정1 `tof-sim_ubai_results_1024/acct1_ema_dz/REPORT.md` §1-1(`eval_real_snap.py`, 센서별 프레임, before17 macro-F1, 괄호 = 전체 단계).

| 모델 | F1 | acc |
|---|---|---|
| opt_l8_c256 best | 0.445 (0.386) | 0.342 |
| dz_v4_weak_aim last | 0.342 (0.296) | 0.216 |
| dz_v5_weak last | 0.340 (0.304) | 0.241 |
| rb_ft_mis07 last | 0.310 (0.270) | 0.223 |
| dz_v5_weak_aim last | 0.183 (0.176) | 0.119 |
| init v5_best | 0.180 | 0.153 |

두 채점 방식 모두 순위 1위는 opt_l8_c256 이다.

## 시뮬에서는 보강 학습이 좋아 보였다 [시뮬]

자세 한 바퀴(posture_tour) 20 시드 정확도. 출처: `tof-sim_ubai_results_1024/laptop_answer_1024.md` §1, 계정1 REPORT §1.

| 모델 | 자세 한 바퀴 정확도 | 약한 끄덕임(15~25°) drowsy 재현 |
|---|---|---|
| opt_l8_c256 best (v4) | 0.813 | 0.29 |
| opt_l8v5_c256 best (v7b) | 0.802 | 0.13 |
| rb_ft_mis07_last | **0.846** | — |
| rb_ftv4_best | 0.826 | — |
| v5_ft_last | 0.831 | — |
| dz_v5_weak_aim last | 0.833 | **0.90** |
| dz_v4_weak_aim last | 0.776 | 0.90 |

- 약한 끄덕임 보강(dz)은 시뮬에서 약한 끄덕임 검출을 0.13 → 0.90 으로 올렸지만 **[실측]** 에서는 0.183~0.342(센서별)로 떨어졌다.
- dz_v5_weak_aim 은 시뮬 writing(숙여 필기) 재현이 0.01 로 무너졌다. rebal 은 writing 0.45 대신 졸음 전조 0.53.

## 파일·재현

- 모델 export(보드용 `posture_net.bin` TOFPN001 · golden · model_card): 팀 저장소 Release `pi4-tof-bundle-20261023`
  (`export_opt_l8_c256_20261022_1910_best.zip` · `export_dz_v5_weak_aim_last.zip` · `export_rb_ft_mis07_last.zip`, sha256 `SHA256SUMS_20261024.txt`).
- 채점 스크립트 `eval_real_snap.py`(같은 Release). 실측 npz 는 직접 녹화한 라벨 데이터라 공개 저장소에 올리지 않는다.
- 근거 원문: PC `handoff_laptop_20261024/pi4_bundle_inputs/README.md`, `tof-sim_ubai_results_1024/laptop_answer_1024.md`, UBAI `~/tof-sim-v5-opt/REPORT_posture_rebal_ps.md`.
