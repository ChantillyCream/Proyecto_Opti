import gurobipy as gp
from gurobipy import GRB
import pandas as pd

#Rutas relativas
RUTA_INSTANCIA = "../Instancias_Excel/instancia.xlsx"
RUTA_SALIDA = "../Análisis_de_Excels/resultados_optimos.xlsx"


def main():
    print("Iniciando carga de datos desde Instancias Excel/...")
    xls = pd.ExcelFile(RUTA_INSTANCIA)

    #Carga de conjuntos
    df_s = pd.read_excel(xls, "SET_S")
    S = df_s["s"].astype(str).tolist()

    df_t = pd.read_excel(xls, "SET_T")
    T = df_t["t"].astype(str).tolist()

    #Capas granulares: 1 = subbase granular, 2 = base granular
    C = [1, 2]

    df_m = pd.read_excel(xls, "SET_M")
    M = df_m["m"].astype(str).tolist()
    #R: Subconjunto de áridos reciclados (ARH)
    R = df_m[df_m["es_reciclado"] == 1]["m"].astype(str).tolist()

    df_f = pd.read_excel(xls, "SET_F")
    F = df_f["f"].astype(str).tolist()
    u_inf = dict(zip(df_f["f"].astype(str), df_f["u_inf"]))
    u_sup = dict(zip(df_f["f"].astype(str), df_f["u_sup"]))

    #Carga de parámetros

    #Propiedades individuales de los materiales
    b_m = dict(zip(df_m["m"].astype(str), df_m["b_m"]))
    k_m = dict(zip(df_m["m"].astype(str), df_m["k_m"]))
    rho_m = dict(zip(df_m["m"].astype(str), df_m["rho_m"]))
    pi_m = dict(zip(df_m["m"].astype(str), df_m["pi_m"]))

    df_gamma = pd.read_excel(xls, "IN_gamma")
    gamma_mf = {
        (str(r["m"]), str(r["f"])): float(r["gamma"])
        for _, r in df_gamma.iterrows()
    }

    #Escalares de diseño y presupuesto
    df_esc = pd.read_excel(xls, "IN_escalares").set_index("parametro")
    h_min = float(df_esc.loc["h_min", "valor"])
    e_min = float(df_esc.loc["e_min", "valor"])
    e_max = float(df_esc.loc["e_max", "valor"])
    p_presupuesto = float(df_esc.loc["p", "valor"])

    #Parámetros del trazado y tramos
    df_tramos = pd.read_excel(xls, "IN_tramos").set_index("s")
    ell_s = df_tramos["l_s"].to_dict()
    w_s = df_tramos["w_s"].to_dict()
    g_s = df_tramos["g_s"].to_dict()
    eps_s = df_tramos["eps_s"].to_dict()
    ac_s = df_tramos["ac_s"].to_dict()

    #Parámetros por capa y tramo (Diseño y Normativa)
    df_capas = pd.read_excel(xls, "IN_capas_tramo")
    a_sc, d_sc, q_sc = {}, {}, {}
    kappa_norma_sc, b_nat_sc = {}, {}
    for _, r in df_capas.iterrows():
        k = (str(r["s"]), int(r["c"]))
        a_sc[k] = float(r["a_sc"])
        d_sc[k] = float(r["d_sc"])
        q_sc[k] = float(r["q_sc"])
        kappa_norma_sc[k] = float(r["kappa_norma"])
        b_nat_sc[k] = float(r["b_nat"])

    #Pre-cálculo de kappa_sc unificado (Manual de Carreteras + Instructivo MOP ORD. 6925)
    kappa_sc = {
        (s, c): max(kappa_norma_sc[s, c], b_nat_sc[s, c])
        for s in S
        for c in C
    }

    #Reemplazo máximo y admisibilidad no aditiva
    df_reemp = pd.read_excel(xls, "IN_reemplazo")
    r_msc = {
        (str(r["m"]), str(r["s"]), int(r["c"])): float(r["r_msc"])
        for _, r in df_reemp.iterrows()
    }

    df_adm = pd.read_excel(xls, "IN_admisibilidad")
    alpha_msc = {
        (str(r["m"]), str(r["s"]), int(r["c"])): int(r["alpha_msc"])
        for _, r in df_adm.iterrows()
    }

    #Bandas granulométricas admisibles y calce
    df_bandas = pd.read_excel(xls, "IN_bandas")
    B_sc = {}
    lambda_mb = {}
    for _, r in df_bandas.iterrows():
        s_val, c_val, b_val = str(r["s"]), int(r["c"]), str(r["b"])
        if (s_val, c_val) not in B_sc:
            B_sc[(s_val, c_val)] = []
        if b_val not in B_sc[(s_val, c_val)]:
            B_sc[(s_val, c_val)].append(b_val)
        lambda_mb[(str(r["m"]), b_val)] = int(r["lambda_mb"])

    #Logística y suministro temporal
    df_log = pd.read_excel(xls, "IN_logistica_t")
    tau_st = {
        (str(r["s"]), str(r["t"])): float(r["tau_st"])
        for _, r in df_log.iterrows()
    }

    df_sum = pd.read_excel(xls, "IN_suministro_t")
    v_mt = {
        (str(r["m"]), str(r["t"])): float(r["v_mt"])
        for _, r in df_sum.iterrows()
    }

    #Inventario inicial en cancha de acopio (0 por defecto)
    im_0_s = {(m, s): 0.0 for m in M for s in S}

    #Cota superior Big-M para precedencia
    Omega_s = {s: sum(tau_st[s, t] for t in T) for s in S}

    #Construcción del modelo en gurobi

    print("Construyendo modelo matemático...")
    model = gp.Model("Optimizacion_Capas_Granulares_RCD")

    #TimeLimit de 30 minutos (1800 s)
    model.setParam("TimeLimit", 1800)
    model.setParam("DualReductions", 0)

    #Variables de decisión
    h = model.addVars(S, C, vtype=GRB.CONTINUOUS, lb=0.0, name="h")
    x = model.addVars(M, S, C, T, vtype=GRB.CONTINUOUS, lb=0.0, name="x")
    y = model.addVars(M, T, S, vtype=GRB.CONTINUOUS, lb=0.0, name="y")
    inv = model.addVars(M, T, S, vtype=GRB.CONTINUOUS, lb=0.0, name="inv")
    n_capas = model.addVars(S, C, vtype=GRB.INTEGER, lb=1, name="n")
    omega = model.addVars(S, T, vtype=GRB.BINARY, name="omega")

    bandas_validas = [
        (s, c, b) for s in S for c in C for b in B_sc.get((s, c), [])
    ]
    z = model.addVars(bandas_validas, vtype=GRB.BINARY, name="z")

    #Función Objetivo (1): Maximizar volumen de árido reciclado colocado
    model.setObjective(
        gp.quicksum(x[m, s, c, t] for m in R for s in S for c in C for t in T),
        GRB.MAXIMIZE,
    )

    #Restricciones
    #(2) y (3) Aporte estructural AASHTO
    for s in S:
        aporte = gp.quicksum(a_sc[s, c] * d_sc[s, c] * h[s, c] for c in C)
        model.addConstr(aporte >= g_s[s], name=f"R02_AporteMin_{s}")
        model.addConstr(
            aporte <= (1.0 + eps_s[s]) * g_s[s], name=f"R03_AporteMax_{s}"
        )

    #(4) y (5) Espesores admisibles y capas constructivas
    for s in S:
        for c in C:
            model.addConstr(h[s, c] >= h_min, name=f"R04_EspesorMin_{s}_{c}")
            model.addConstr(
                h[s, c] >= e_min * n_capas[s, c], name=f"R05a_CapaMin_{s}_{c}"
            )
            model.addConstr(
                h[s, c] <= e_max * n_capas[s, c], name=f"R05b_CapaMax_{s}_{c}"
            )

    #(6) Balance geométrico de volumen
    for s in S:
        for c in C:
            vol_colocado = gp.quicksum(x[m, s, c, t] for m in M for t in T)
            vol_req = (ell_s[s] * w_s[s] / 1000.0) * h[s, c]
            model.addConstr(
                vol_colocado == vol_req, name=f"R06_BalanceVol_{s}_{c}"
            )

    #(7) Capacidad de soporte CBR de la mezcla por período
    for s in S:
        for c in C:
            for t in T:
                lhs = gp.quicksum(b_m[m] * x[m, s, c, t] for m in M)
                rhs = kappa_sc[s, c] * gp.quicksum(x[m, s, c, t] for m in M)
                model.addConstr(
                    lhs >= rhs, name=f"R07_CBR_Mezcla_{s}_{c}_{t}"
                )

    #(8) y (9) Balance e inventario en cancha de acopio
    for m in M:
        for s in S:
            t1 = T[0]
            model.addConstr(
                inv[m, t1, s]
                == im_0_s[m, s]
                + y[m, t1, s]
                - gp.quicksum(x[m, s, c, t1] for c in C),
                name=f"R08_Inv_{m}_{s}_{t1}",
            )
            for idx in range(1, len(T)):
                t_act, t_ant = T[idx], T[idx - 1]
                model.addConstr(
                    inv[m, t_act, s]
                    == inv[m, t_ant, s]
                    + y[m, t_act, s]
                    - gp.quicksum(x[m, s, c, t_act] for c in C),
                    name=f"R09_Inv_{m}_{s}_{t_act}",
                )

    #(10) Acopio considerando inventario inicial + despacho periodo siguiente
    # \sum_{m \in M} (i_{mts} + y_{m, t+1, s}) <= ac_s  \forall s \in S, \forall t = 1, ..., T-1
    for s in S:
        for idx in range(len(T) - 1):
            t_act = T[idx]
            t_sig = T[idx + 1]
            model.addConstr(
                gp.quicksum(inv[m, t_act, s] + y[m, t_sig, s] for m in M) <= ac_s[s],
                name=f"R10_Cancha_{s}_{t_act}",
            )

    #(11) Oferta mensual disponible en plantas de reciclaje
    for m in R:
        for t in T:
            model.addConstr(
                gp.quicksum(y[m, t, s] for s in S) <= v_mt[m, t],
                name=f"R11_OfertaRCD_{m}_{t}",
            )

    #(12) Tasa máxima de colocación y compactación en pista
    for s in S:
        for t in T:
            model.addConstr(
                gp.quicksum(x[m, s, c, t] for c in C for m in M)
                <= tau_st[s, t],
                name=f"R12_Pista_{s}_{t}",
            )

    #(13) y (14) Precedencia constructiva (Base c=2 tras finalizar Subbase c=1)
    for s in S:
        for idx, t in enumerate(T):
            t_anteriores = T[:idx]
            vol_sub_prev = gp.quicksum(
                x[m, s, 1, tp] for m in M for tp in t_anteriores
            )
            vol_sub_tot = gp.quicksum(x[m, s, 1, tp] for m in M for tp in T)

            model.addConstr(
                vol_sub_prev >= vol_sub_tot - Omega_s[s] * (1.0 - omega[s, t]),
                name=f"R13_Precedencia_{s}_{t}",
            )
            model.addConstr(
                gp.quicksum(x[m, s, 2, t] for m in M)
                <= tau_st[s, t] * omega[s, t],
                name=f"R14_BloqueoBase_{s}_{t}",
            )

    #(15) y (16) Clasificación granulométrica y admisibilidad
    for s in S:
        for c in C:
            model.addConstr(
                gp.quicksum(z[s, c, b] for b in B_sc.get((s, c), [])) == 1,
                name=f"R15_UnicaBanda_{s}_{c}",
            )
            for m in M:
                for t in T:
                    calza = gp.quicksum(
                        lambda_mb.get((m, b), 0) * z[s, c, b]
                        for b in B_sc.get((s, c), [])
                    )
                    model.addConstr(
                        x[m, s, c, t]
                        <= tau_st[s, t] * alpha_msc[m, s, c] * calza,
                        name=f"R16_Adm_{m}_{s}_{c}_{t}",
                    )

    #(17) Límite máximo de reemplazo por árido reciclado
    for m in R:
        for s in S:
            for c in C:
                lhs = gp.quicksum(x[m, s, c, t] for t in T)
                rhs = r_msc[m, s, c] * gp.quicksum(
                    x[mp, s, c, t] for mp in M for t in T
                )
                model.addConstr(
                    lhs <= rhs, name=f"R17_MaxReemplazo_{m}_{s}_{c}"
                )

    #(18) Fracción mínima de material chancado
    for s in S:
        for c in C:
            lhs = gp.quicksum(
                k_m[m] * x[m, s, c, t] * rho_m[m] for m in M for t in T
            )
            rhs = q_sc[s, c] * gp.quicksum(
                x[m, s, c, t] * rho_m[m] for m in M for t in T
            )
            model.addConstr(lhs >= rhs, name=f"R18_Chancado_{s}_{c}")

    #(19) y (20) Fracciones normativas de pureza del material reciclado
    for f in F:
        for s in S:
            for c in C:
                m_frac = gp.quicksum(
                    gamma_mf[m, f] * x[m, s, c, t] * rho_m[m]
                    for m in M
                    for t in T
                )
                m_tot = gp.quicksum(
                    x[m, s, c, t] * rho_m[m] for m in M for t in T
                )
                model.addConstr(
                    m_frac <= u_sup[f] * m_tot, name=f"R19_PurezaSup_{f}_{s}_{c}"
                )
                model.addConstr(
                    m_frac >= u_inf[f] * m_tot, name=f"R20_PurezaInf_{f}_{s}_{c}"
                )

    #(21) Presupuesto disponible para capas granulares
    costo_total = gp.quicksum(
        pi_m[m] * x[m, s, c, t] for m in M for s in S for c in C for t in T
    )
    model.addConstr(costo_total <= p_presupuesto, name="R21_Presupuesto")

    #Resolución

    print("Resolviendo modelo con Gurobi...")
    model.optimize()

    #Exportación a Excel
    if model.SolCount > 0:
        print(f"\nSolución obtenida. Exportando a '{RUTA_SALIDA}'...")

        res_espesores = []
        for s in S:
            for c in C:
                res_espesores.append(
                    {
                        "Tramo": s,
                        "Capa": "Subbase" if c == 1 else "Base",
                        "Espesor_mm": h[s, c].X,
                        "Num_Capas": int(n_capas[s, c].X),
                    }
                )

        res_colocacion = []
        for s in S:
            for c in C:
                for t in T:
                    for m in M:
                        if x[m, s, c, t].X > 1e-3:
                            res_colocacion.append(
                                {
                                    "Tramo": s,
                                    "Capa": "Subbase" if c == 1 else "Base",
                                    "Periodo": t,
                                    "Material": m,
                                    "Volumen_m3": x[m, s, c, t].X,
                                }
                            )

        res_resumen = [
            {"Métrica": "Valor Objetivo (RCD Total m³)", "Valor": model.ObjVal},
            {"Métrica": "Costo Total ($ CLP)", "Valor": costo_total.getValue()},
            {"Métrica": "Presupuesto Disponible ($ CLP)", "Valor": p_presupuesto},
            {"Métrica": "MIPGap (%)", "Valor": model.MIPGap * 100},
            {"Métrica": "Tiempo de cómputo (s)", "Valor": model.Runtime},
        ]

        with pd.ExcelWriter(RUTA_SALIDA) as writer:
            pd.DataFrame(res_resumen).to_excel(
                writer, sheet_name="Resumen_General", index=False
            )
            pd.DataFrame(res_espesores).to_excel(
                writer, sheet_name="Espesores_Optimos", index=False
            )
            pd.DataFrame(res_colocacion).to_excel(
                writer, sheet_name="Plan_Colocacion_x", index=False
            )

        print(">>> Archivo de resultados generado exitosamente.")
    elif model.status == GRB.INFEASIBLE:
        print("\n>>> El modelo es INFACTIBLE. Calculando IIS (restricciones en conflicto)...")
        model.computeIIS()
        print("\n" + "="*60)
        print("RESTRICCIONES EXACTAS QUE PROVOCAN EL CONFLICTO:")
        print("="*60)
        for c in model.getConstrs():
            if c.IISConstr:
                print(f"  ❌ Restricción: {c.ConstrName}")
        for v in model.getVars():
            if v.IISLB:
                print(f"  ❌ Cota inferior: {v.VarName}")
            if v.IISUB:
                print(f"  ❌ Cota superior: {v.VarName}")
        print("="*60)
        model.write("infeasible.ilp")
        print("Se guardó el archivo de conflicto 'infeasible.ilp' para inspección.")


if __name__ == "__main__":
    main()