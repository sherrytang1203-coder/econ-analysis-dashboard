import yfinance as yf

for ticker in ['JRVR', 'MU', 'BN']:
    print(f"\n=== {ticker} ===")
    t = yf.Ticker(ticker)
    income_stmt = t.income_stmt
    cash_flow = t.cash_flow

    # Check revenue column
    revenue_col = None
    for col_name in ["Total Revenue", "TotalRevenue", "Revenue"]:
        if col_name in income_stmt.index:
            revenue_col = col_name
            break

    print(f"Revenue column: {revenue_col}")

    if revenue_col:
        revenues = income_stmt.loc[revenue_col].dropna().head(3)
        print(f"Revenues shape: {revenues.shape}")
        print(f"Avg revenue: {float(revenues.mean())}")

        # Check FCF
        fcf_col = None
        for col_name in ["Free Cash Flow", "FreeCashFlow"]:
            if col_name in cash_flow.index:
                fcf_col = col_name
                break

        print(f"FCF column: {fcf_col}")

        if not fcf_col:
            # Try OCF - CapEx
            ocf_col = None
            for col_name in ["Operating Cash Flow", "Total Cash From Operating Activities"]:
                if col_name in cash_flow.index:
                    ocf_col = col_name
                    break

            print(f"OCF column: {ocf_col}")

            if ocf_col:
                capex_col = None
                for col_name in ["Capital Expenditure", "Capital Expenditures"]:
                    if col_name in cash_flow.index:
                        capex_col = col_name
                        break

                print(f"CapEx column: {capex_col}")
                print(f"Can calculate FCF: {ocf_col and capex_col}")
