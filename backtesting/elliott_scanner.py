import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)

def calc_atr(df, period=14):
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift()).abs()
    low_close = (df['low'] - df['close'].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()

def get_adaptive_zigzag(df, atr_multiplier=1.5):
    """
    Extracts macro swings (pivots) using ATR-based retracement.
    Returns a list of dicts: [{'time': ts, 'price': p, 'type': 'high'|'low', 'idx': i}, ...]
    """
    if len(df) < 20:
        return []
        
    df = df.copy()
    if 'atr' not in df.columns:
        df['atr'] = calc_atr(df)
        
    df['atr'] = df['atr'].bfill()
    
    pivots = []
    
    # Initialize state
    last_high_idx = 0
    last_low_idx = 0
    last_high_price = df['high'].iloc[0]
    last_low_price = df['low'].iloc[0]
    
    # 1 for looking for High, -1 for looking for Low
    direction = 1 if df['close'].iloc[1] > df['close'].iloc[0] else -1
    
    for i in range(1, len(df)):
        row = df.iloc[i]
        curr_atr = row['atr']
        threshold = curr_atr * atr_multiplier
        
        if direction == 1:
            # Looking for a High
            if row['high'] > last_high_price:
                last_high_price = row['high']
                last_high_idx = i
            elif row['high'] < last_high_price - threshold:
                # Retracement confirmed! The last high is a valid pivot.
                pivots.append({
                    'time': df.iloc[last_high_idx]['timestamp'],
                    'price': last_high_price,
                    'type': 'high',
                    'idx': last_high_idx
                })
                # Switch direction
                direction = -1
                last_low_price = row['low']
                last_low_idx = i
                
        elif direction == -1:
            # Looking for a Low
            if row['low'] < last_low_price:
                last_low_price = row['low']
                last_low_idx = i
            elif row['low'] > last_low_price + threshold:
                # Retracement confirmed! The last low is a valid pivot.
                pivots.append({
                    'time': df.iloc[last_low_idx]['timestamp'],
                    'price': last_low_price,
                    'type': 'low',
                    'idx': last_low_idx
                })
                # Switch direction
                direction = 1
                last_high_price = row['high']
                last_high_idx = i
                
    # Add the current extreme as the active pivot
    if direction == 1:
        pivots.append({
            'time': df.iloc[last_high_idx]['timestamp'],
            'price': last_high_price,
            'type': 'high',
            'idx': last_high_idx
        })
    else:
        pivots.append({
            'time': df.iloc[last_low_idx]['timestamp'],
            'price': last_low_price,
            'type': 'low',
            'idx': last_low_idx
        })
        
    return pivots

def evaluate_elliott_wave(pivots, current_price):
    """
    Evaluates the last sequence of pivots for Elliott Wave rules.
    We look at the last N pivots (e.g., 6 for a full 5-wave, or 8 for 5-wave + ABC).
    Returns the best possible count update or None.
    """
    if len(pivots) < 4:
        return None
        
    # We will test the last 4, 5, 6, 8 pivots to see what fits best.
    # A standard 5-wave impulse has 6 points: 0, 1, 2, 3, 4, 5
    # Wave 1 is points[0] to points[1]
    # Wave 2 is points[1] to points[2]
    # Wave 3 is points[2] to points[3]
    # Wave 4 is points[3] to points[4]
    # Wave 5 is points[4] to points[5]
    
    valid_counts = []
    
    # Try different slice lengths representing the start of the wave pattern
    for start_idx in range(max(0, len(pivots) - 10), len(pivots) - 3):
        seq = pivots[start_idx:]
        n = len(seq)
        
        # Determine trend (Bullish if 0 is low and 1 is high)
        is_bullish = seq[0]['type'] == 'low'
        
        p0 = seq[0]['price']
        p1 = seq[1]['price']
        p2 = seq[2]['price']
        p3 = seq[3]['price'] if n > 3 else None
        p4 = seq[4]['price'] if n > 4 else None
        p5 = seq[5]['price'] if n > 5 else None
        
        # Rule 1: Wave 2 cannot retrace below start of Wave 1
        w1_len = abs(p1 - p0)
        w2_retrace = p1 - p2 if is_bullish else p2 - p1
        if is_bullish and p2 <= p0: continue
        if not is_bullish and p2 >= p0: continue
        
        # If we only have 0, 1, 2, 3 -> forming_wave_4 or forming_wave_3
        # If we have 0, 1, 2, 3, 4 -> forming_wave_5
        
        # We need p3 to validate rule 2 & 3
        if p3 is None:
            # Status: forming wave 3
            # We assume current price is wave 3 forming
            w3_len_so_far = current_price - p2 if is_bullish else p2 - current_price
            conf = 50
            fibo_targets = [
                {'price': p2 + w1_len * 1.618 if is_bullish else p2 - w1_len * 1.618, 'level': '1.618'},
                {'price': p2 + w1_len * 2.618 if is_bullish else p2 - w1_len * 2.618, 'level': '2.618'}
            ]
            valid_counts.append({
                'points': seq + [{'time': int(pd.Timestamp.now().timestamp()), 'price': current_price, 'label': '3 (Active)'}],
                'labeled_points': [{'time': seq[i]['time'], 'price': seq[i]['price'], 'label': str(i)} for i in range(len(seq))],
                'status': 'forming_wave_3',
                'confidence': conf,
                'invalidation_price': p0,
                'fibo_targets': fibo_targets
            })
            continue
            
        w3_len = abs(p3 - p2)
        
        if p4 is None:
            # Status: forming wave 4
            # Current price is forming wave 4. 
            # Rule: Wave 4 cannot overlap with Wave 1 entirely (though some allow wick overlap)
            # Invalidation price = p1 (top of Wave 1)
            invalidation = p1
            if is_bullish and current_price <= p1: continue
            if not is_bullish and current_price >= p1: continue
            
            fibo_targets = [
                {'price': p3 - w3_len * 0.382 if is_bullish else p3 + w3_len * 0.382, 'level': '0.382'},
                {'price': p3 - w3_len * 0.5 if is_bullish else p3 + w3_len * 0.5, 'level': '0.500'}
            ]
            
            # Confidence based on w3 vs w1
            conf = 60
            if abs(w3_len / w1_len - 1.618) < 0.2: conf += 20
            
            valid_counts.append({
                'points': seq + [{'time': int(pd.Timestamp.now().timestamp()), 'price': current_price, 'label': '4 (Active)'}],
                'labeled_points': [{'time': seq[i]['time'], 'price': seq[i]['price'], 'label': str(i)} for i in range(len(seq))],
                'status': 'forming_wave_4',
                'confidence': conf,
                'invalidation_price': invalidation,
                'fibo_targets': fibo_targets
            })
            continue
            
        # Rule 3: Wave 4 cannot overlap with Wave 1
        if is_bullish and p4 <= p1: continue
        if not is_bullish and p4 >= p1: continue
        
        w4_retrace = abs(p3 - p4)
        
        if p5 is None:
            # Status: forming wave 5
            # We know 0, 1, 2, 3, 4 are confirmed.
            # Invalidation: If wave 5 breaks below wave 4 (p4)
            invalidation = p4
            if is_bullish and current_price <= p4: continue
            if not is_bullish and current_price >= p4: continue
            
            # Additional Rule for impending wave 5: Wave 3 cannot be the shortest.
            # since 5 hasn't formed yet, we can't fully check rule 2.
            
            fibo_targets = [
                {'price': p4 + w1_len * 0.618 if is_bullish else p4 - w1_len * 0.618, 'level': '0.618'},
                {'price': p4 + w1_len * 1.0 if is_bullish else p4 - w1_len * 1.0, 'level': '1.000'}
            ]
            
            conf = 70
            # Alternation rule: If wave 2 was deep (>61.8%), wave 4 should be shallow (<38.2%), vice versa.
            w2_pct = w2_retrace / w1_len if w1_len > 0 else 0
            w4_pct = w4_retrace / w3_len if w3_len > 0 else 0
            if (w2_pct > 0.5 and w4_pct < 0.5) or (w2_pct < 0.5 and w4_pct > 0.5):
                conf += 15
                
            valid_counts.append({
                'points': seq + [{'time': int(pd.Timestamp.now().timestamp()), 'price': current_price, 'label': '5 (Active)'}],
                'labeled_points': [{'time': seq[i]['time'], 'price': seq[i]['price'], 'label': str(i)} for i in range(len(seq))],
                'status': 'forming_wave_5',
                'confidence': conf,
                'invalidation_price': invalidation,
                'fibo_targets': fibo_targets
            })
            continue

        w5_len = abs(p5 - p4)
        
        # Rule 2: Wave 3 cannot be shortest
        if w3_len < w1_len and w3_len < w5_len:
            continue
            
        # If we have 0, 1, 2, 3, 4, 5 -> forming ABC
        pA = seq[6]['price'] if n > 6 else None
        pB = seq[7]['price'] if n > 7 else None
        
        if pA is None:
            # Status: forming wave A
            # Invalidation: wave A goes above wave 5 (which is the peak)
            invalidation = p5
            fibo_targets = [
                {'price': p5 - (p5-p0)*0.382 if is_bullish else p5 + (p0-p5)*0.382, 'level': '0.382 Macro'},
                {'price': p5 - (p5-p0)*0.618 if is_bullish else p5 + (p0-p5)*0.618, 'level': '0.618 Macro'}
            ]
            valid_counts.append({
                'points': seq + [{'time': int(pd.Timestamp.now().timestamp()), 'price': current_price, 'label': 'A (Active)'}],
                'labeled_points': [{'time': seq[i]['time'], 'price': seq[i]['price'], 'label': str(i)} for i in range(min(6, len(seq)))], # Only label 0-5
                'status': 'forming_wave_A',
                'confidence': 80, # Confirmation of 5 wave structure gives high confidence
                'invalidation_price': invalidation,
                'fibo_targets': fibo_targets
            })
            
    # Sort counts by confidence
    valid_counts.sort(key=lambda x: x['confidence'], reverse=True)
    return valid_counts

def scan_elliott_waves(df, current_price, atr_multiplier=1.5):
    """
    Returns the top Elliott Wave counts with targets and invalidations.
    Output: dictionary ready for WebSocket emission.
    """
    try:
        if len(df) < 50:
            return None
            
        # 1. Fetch Pivots via ATR ZigZag
        pivots = get_adaptive_zigzag(df, atr_multiplier)
        if not pivots:
            return None
            
        # 2. Evaluate rules
        counts = evaluate_elliott_wave(pivots, current_price)
        if not counts:
            return None
            
        # 3. Pick top 3 counts 
        top_counts = counts[:3]
        best_count = top_counts[0]
        
        # Format points nicely
        formatted_labeled_points = best_count['labeled_points']
        
        payload = {
            'points': best_count['points'],
            'labeled_points': formatted_labeled_points,
            'status': best_count['status'],
            'confidence': best_count['confidence'],
            'invalidation_price': round(best_count['invalidation_price'], 2),
            'fibo_targets': [{'price': round(t['price'], 2), 'level': t['level']} for t in best_count['fibo_targets']],
            'alternative_counts': len(top_counts) - 1
        }
        return payload
    except Exception as e:
        logger.error(f"Elliott Wave scan error: {e}")
        return None
