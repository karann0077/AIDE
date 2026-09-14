# SPICE Building Blocks Library
# ==============================
# Pre-verified sub-circuits for LTspice XVII (macOS, LEVEL=3 MOSFETs)
# The Circuit Generator LLM uses these as assembly primitives.
#
# RULES (always enforced):
#  1. DC and TRAN must be separate netlists — never in one file
#  2. No curly braces {} in .meas PARAM expressions
#  3. Use LEVEL=3 MOSFET models below — no external PDK needed
#  4. All tunable widths/lengths must be .param
#  5. Output nodes must use readable names (out, sum, cout, q, qb, etc.)

# ─────────────────────────────────────────────────────────────
# STANDARD MODEL CARDS  (paste at bottom of every netlist)
# ─────────────────────────────────────────────────────────────
# .model NMOS NMOS (LEVEL=3 TOX=4e-9 VTO=0.5 UO=450 THETA=0.1 KAPPA=0.3 ETA=0.01 NSUB=1e17 LD=5n WD=5n)
# .model PMOS PMOS (LEVEL=3 TOX=4e-9 VTO=-0.5 UO=150 THETA=0.1 KAPPA=0.3 ETA=0.01 NSUB=1e17 LD=5n WD=5n)

# ─────────────────────────────────────────────────────────────
# INVERTER
# ─────────────────────────────────────────────────────────────
# .subckt INV IN OUT VDD VSS WN=2u WP=4u LN=180n LP=180n
# MP1 OUT IN VDD VDD PMOS W=WP L=LP
# MN1 OUT IN VSS VSS NMOS W=WN L=LN
# .ends INV

# ─────────────────────────────────────────────────────────────
# NAND2
# ─────────────────────────────────────────────────────────────
# .subckt NAND2 A B OUT VDD VSS WN=2u WP=4u LN=180n LP=180n
# MP1 OUT A VDD VDD PMOS W=WP L=LP
# MP2 OUT B VDD VDD PMOS W=WP L=LP
# MN1 OUT A mid VSS NMOS W=WN L=LN
# MN2 mid B VSS VSS NMOS W=WN L=LN
# .ends NAND2

# ─────────────────────────────────────────────────────────────
# NOR2
# ─────────────────────────────────────────────────────────────
# .subckt NOR2 A B OUT VDD VSS WN=2u WP=4u LN=180n LP=180n
# MP1 OUT A mid VDD PMOS W=WP L=LP
# MP2 mid B VDD VDD PMOS W=WP L=LP
# MN1 OUT A VSS VSS NMOS W=WN L=LN
# MN2 OUT B VSS VSS NMOS W=WN L=LN
# .ends NOR2

# ─────────────────────────────────────────────────────────────
# NAND3
# ─────────────────────────────────────────────────────────────
# .subckt NAND3 A B C OUT VDD VSS WN=3u WP=4u LN=180n LP=180n
# MP1 OUT A VDD VDD PMOS W=WP L=LP
# MP2 OUT B VDD VDD PMOS W=WP L=LP
# MP3 OUT C VDD VDD PMOS W=WP L=LP
# MN1 OUT A m1  VSS NMOS W=WN L=LN
# MN2 m1  B m2  VSS NMOS W=WN L=LN
# MN3 m2  C VSS VSS NMOS W=WN L=LN
# .ends NAND3

# ─────────────────────────────────────────────────────────────
# XOR2  (using 4 NAND gates — standard CMOS)
# ─────────────────────────────────────────────────────────────
# .subckt XOR2 A B OUT VDD VSS WN=2u WP=4u LN=180n LP=180n
# * NAND(A,B) → n1
# MP_n1a n1 A VDD VDD PMOS W=WP L=LP
# MP_n1b n1 B VDD VDD PMOS W=WP L=LP
# MN_n1a n1 A mid_n1 VSS NMOS W=WN L=LN
# MN_n1b mid_n1 B VSS VSS NMOS W=WN L=LN
# * NAND(A,n1) → n2
# MP_n2a n2 A VDD VDD PMOS W=WP L=LP
# MP_n2b n2 n1 VDD VDD PMOS W=WP L=LP
# MN_n2a n2 A mid_n2 VSS NMOS W=WN L=LN
# MN_n2b mid_n2 n1 VSS VSS NMOS W=WN L=LN
# * NAND(B,n1) → n3
# MP_n3a n3 B VDD VDD PMOS W=WP L=LP
# MP_n3b n3 n1 VDD VDD PMOS W=WP L=LP
# MN_n3a n3 B mid_n3 VSS NMOS W=WN L=LN
# MN_n3b mid_n3 n1 VSS VSS NMOS W=WN L=LN
# * NAND(n2,n3) → OUT
# MP_oa OUT n2 VDD VDD PMOS W=WP L=LP
# MP_ob OUT n3 VDD VDD PMOS W=WP L=LP
# MN_oa OUT n2 mid_o VSS NMOS W=WN L=LN
# MN_ob mid_o n3 VSS VSS NMOS W=WN L=LN
# .ends XOR2

# ─────────────────────────────────────────────────────────────
# XNOR2
# ─────────────────────────────────────────────────────────────
# .subckt XNOR2 A B OUT VDD VSS WN=2u WP=4u LN=180n LP=180n
# * XOR then invert
# XXOR xor_out A B vdd vss WN=WN WP=WP LN=LN LP=LP XOR2
# MP_inv OUT xor_out VDD VDD PMOS W=WP L=LP
# MN_inv OUT xor_out VSS VSS NMOS W=WN L=LN
# .ends XNOR2

# ─────────────────────────────────────────────────────────────
# D FLIP-FLOP (master-slave, transmission gate style)
# ─────────────────────────────────────────────────────────────
# .subckt DFF D CLK Q QB VDD VSS WN=2u WP=4u LN=180n LP=180n
# * Transmission gate master latch
# MTG_MP1 D Q_m CLK VDD PMOS W=WP L=LP
# MTG_MN1 D Q_m CLKB VSS NMOS W=WN L=LN
# * Inverter feedback
# MFB_P Q_m Q_mfb VDD VDD PMOS W=WP L=LP
# MFB_N Q_m Q_mfb VSS VSS NMOS W=WN L=LN
# MTG_MP2 Q_mfb Q_m CLKB VDD PMOS W=WP L=LP
# MTG_MN2 Q_mfb Q_m CLK VSS NMOS W=WN L=LN
# * Clock inverter
# MCLK_P CLKB CLK VDD VDD PMOS W=WP L=LP
# MCLK_N CLKB CLK VSS VSS NMOS W=WN L=LN
# * Slave latch
# MTG_MP3 Q_m Q CLKB VDD PMOS W=WP L=LP
# MTG_MN3 Q_m Q CLK VSS NMOS W=WN L=LN
# MO_P QB Q VDD VDD PMOS W=WP L=LP
# MO_N QB Q VSS VSS NMOS W=WN L=LN
# MO2_P Q QB VDD VDD PMOS W=WP L=LP
# MO2_N Q QB VSS VSS NMOS W=WN L=LN
# .ends DFF

# ─────────────────────────────────────────────────────────────
# CURRENT MIRROR (basic)
# ─────────────────────────────────────────────────────────────
# .subckt CMIRROR IREF IOUT VDD WN=4u LN=500n
# MR  IREF IREF VDD VDD PMOS W=WN L=LN
# MO  IOUT IREF VDD VDD PMOS W=WN L=LN
# .ends CMIRROR

# ─────────────────────────────────────────────────────────────
# DIFF PAIR (for OTA)
# ─────────────────────────────────────────────────────────────
# .subckt DIFFPAIR INP INN OUTP OUTN VDD TAIL WP=8u LP=500n WN=4u LN=500n
# MP1 OUTN INP TAIL TAIL PMOS W=WP L=LP
# MP2 OUTP INN TAIL TAIL PMOS W=WP L=LP
# MN1 OUTN OUTN VDD VDD NMOS W=WN L=LN
# MN2 OUTP OUTN VDD VDD NMOS W=WN L=LN
# .ends DIFFPAIR
