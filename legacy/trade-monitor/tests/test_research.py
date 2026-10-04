import unittest
import numpy as np
import pandas as pd
from research.study import clean_bars, features, event_outcomes, block_interval


def sample(n=220):
    index=pd.bdate_range('2025-01-01',periods=n)
    close=100+np.arange(n)*0.1+np.sin(np.arange(n)/4)
    return pd.DataFrame(dict(open=close-.1,high=close+1,low=close-1,close=close,volume=np.full(n,1000.)),index=index)


class ResearchTests(unittest.TestCase):
    def test_daily_features_use_prior_completed_session(self):
        d=sample(10);d['close']=np.arange(1.,11.);d['open']=d.close;d['high']=d.close+1;d['low']=d.close-.5
        f=features(d)
        self.assertAlmostEqual(f.iloc[7].ma5,5.)
        self.assertAlmostEqual(f.iloc[7].slope1,1.)
        self.assertAlmostEqual(f.iloc[7].slope3,1.)
        self.assertAlmostEqual(f.iloc[7].atr5,2.)

    def test_future_tail_and_prefix_do_not_change_features(self):
        d=sample();f=features(d)
        altered=d.copy();altered.iloc[170:,:4]*=9
        pd.testing.assert_frame_equal(f.iloc[:170],features(altered).iloc[:170])
        pd.testing.assert_frame_equal(f.iloc[:170],features(d.iloc[:170]))

    def test_invalid_ohlc_quarantines_whole_row_and_dependent_window(self):
        d=sample();d.loc[d.index[20],'high']=1
        clean,bad=clean_bars(d)
        self.assertEqual(int(bad.sum()),1)
        self.assertTrue(clean.iloc[20].isna().all())
        self.assertTrue(np.isnan(features(clean).iloc[21].ma5))

    def test_outcomes_enter_next_open_and_exclude_signal_extreme(self):
        d=sample(6);d.loc[d.index[0],'low']=0.01
        d.loc[d.index[1],['open','high','low','close']]=[100,120,90,110]
        mask=pd.Series([True,False,False,False,False,True],index=d.index)
        rows,counts=event_outcomes(d,mask,1)
        self.assertEqual(len(rows),1)
        self.assertAlmostEqual(rows[0]['gross'],.1)
        self.assertAlmostEqual(rows[0]['mae'],-.1)
        self.assertAlmostEqual(rows[0]['mfe'],.2)
        self.assertEqual(counts['right_censored'],1)

    def test_nonoverlap_and_missing_outcome_not_silently_counted(self):
        d=sample(12);mask=pd.Series([True,False]*6,index=d.index)
        rows,c=event_outcomes(d,mask,3)
        self.assertEqual([r['signal_i'] for r in rows],[0,4,8])
        broken=d.copy();broken.iloc[1]=np.nan
        rows,c=event_outcomes(broken,mask,3)
        self.assertGreater(c['invalid_outcome'],0)
        self.assertNotIn(0,[r['signal_i'] for r in rows])

    def test_constant_block_interval_is_exact(self):
        x=np.full(100,.02)
        lo,hi=block_interval(x,draws=100)
        self.assertAlmostEqual(lo,.02);self.assertAlmostEqual(hi,.02)

    def test_signal_on_exit_day_can_enter_next_open(self):
        d=sample(8)
        mask=pd.Series([True,False,False,True,False,False,True,False],index=d.index)
        rows,counts=event_outcomes(d,mask,3)
        self.assertEqual([r['signal_i'] for r in rows],[0,3])
        self.assertEqual(counts['right_censored'],1)

    def test_same_stock_control_subtracts_asset_selection_return(self):
        from research.diagnostics import same_stock_control
        a=np.full((40,2),np.nan);a[::4,0]=.02;a[::8,1]=.10
        b=np.tile([.02,.10],(40,1))
        result=same_stock_control(a,b,draws=100)
        self.assertAlmostEqual(result['signal_gross'],(.02*10+.10*5)/15)
        self.assertAlmostEqual(result['increment'],0.)
        np.testing.assert_allclose(result['ci_increment'],[0,0],atol=1e-12)
