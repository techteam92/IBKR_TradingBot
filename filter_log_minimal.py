def filter_ib_log_minimal(input_file, output_file):
    """
    Filter IB.log to keep ONLY:
    - When order fills: "Market order filled" with params
    - Calculation results: "tp calculation", "sl calculation", "Extended TP calculation", "Extended hours stop"
    - When new orders triggered: "Sending TP Trade", "Sending STPLOSS Trade", "Sending Moc Order" with params
    - New order placement: placeOrder for TP/SL/MOC orders only (identified by ocaGroup or TakeProfit/StopLoss in StatusUpdate)
    """
    
    # Keep only these specific patterns
    keep_patterns = [
        'Market order filled',        # When order fills
        'tp calculation',             # TP calculation results
        'sl calculation',             # SL calculation results
        'ATR stop loss',              # ATR stop loss calculations
        'Custom stop loss',           # Custom stop loss calculations
        'BUY stop loss',              # BUY stop loss (for SHORT positions)
        'SELL stop loss',             # SELL stop loss (for LONG positions)
        'Extended TP calculation',    # Extended hours TP calculation
        'Extended hours stop',        # Extended hours stop calculation
        'Extended hours ATR stop',    # ATR stop calculation
        'Extended hours stop-limit',  # Stop-limit calculation
        'Extended hours protection stop-limit',  # Protection stop-limit
        'ATR stop offset',            # ATR stop offset calculation
        'Sending TP Trade',           # TP order triggered
        'Sending STPLOSS Trade',      # SL order triggered
        'Sending Moc Order',          # MOC order triggered
    ]
    
    filtered_lines = []
    total_lines = 0
    kept_lines = 0
    
    print(f"Reading {input_file}...")
    
    with open(input_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    i = 0
    while i < len(lines):
        line = lines[i]
        total_lines += 1
        line_lower = line.lower()
        
        # Check if this line matches any keep pattern
        matches_pattern = any(pattern.lower() in line_lower for pattern in keep_patterns)
        
        # Check if this is a placeOrder line for TP/SL/MOC (not Entry)
        is_tp_sl_placeorder = False
        if 'placeorder' in line_lower:
            # Check if it has ocaGroup (TP/SL/MOC orders have ocaGroup, Entry orders don't)
            if "ocagroup" in line_lower or "ocaGroup" in line or "'tp" in line_lower or '"tp' in line_lower:
                # Make sure it's NOT explicitly an Entry order
                # Look for context to confirm it's TP/SL/MOC
                context_window = lines[max(0, i-10):min(len(lines), i+5)]
                context_text = ' '.join(context_window).lower()
                
                # If context mentions TakeProfit, StopLoss, or follows "Sending TP/STPLOSS/Moc", keep it
                if any(keyword in context_text for keyword in ['takeprofit', 'stoploss', 'marketonclose', 'sending tp', 'sending stp', 'sending moc', 'ordtype.*takeprofit', 'ordtype.*stoploss']):
                    is_tp_sl_placeorder = True
                # Also keep if it has ocaGroup and not Entry
                elif 'ocagroup' in line_lower and "'ordtype': 'entry'" not in line_lower:
                    is_tp_sl_placeorder = True
        
        # Check if StatusUpdate mentions TakeProfit/StopLoss/Marketonclose (but not Entry)
        is_tp_sl_statusupdate = False
        if 'statusupdate' in line_lower or 'statusUpdate' in line:
            if any(keyword in line_lower for keyword in ["ordtype.*takeprofit", "ordtype.*stoploss", "ordtype.*marketonclose", "'takeprofit'", "'stoploss'", "'marketonclose'"]):
                if "'entry'" not in line_lower or "ordtype.*entry" not in line_lower:
                    is_tp_sl_statusupdate = True
        
        if matches_pattern or is_tp_sl_placeorder or is_tp_sl_statusupdate:
            filtered_lines.append(line)
            kept_lines += 1
        
        i += 1
    
    print(f"Total lines: {total_lines}")
    print(f"Kept lines: {kept_lines}")
    print(f"Removed lines: {total_lines - kept_lines}")
    
    print(f"\nWriting filtered log to {output_file}...")
    with open(output_file, 'w', encoding='utf-8') as f:
        f.writelines(filtered_lines)
    
    print(f"Done! Filtered log saved to {output_file}")

if __name__ == "__main__":
    filter_ib_log_minimal("IB.log.backup", "IB.log")

