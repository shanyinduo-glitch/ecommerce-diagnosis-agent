SELECT i.period,
       i.channel,
       SUM(i.net_cents)/100.0 AS net_sales,
       COUNT(DISTINCT i.order_id) AS order_count,
       SUM(i.pre_refund_cents)/100.0 AS pre_refund_sales,
       SUM(i.refund_amount_cents)/100.0 AS refund_amount,
       SUM(i.gross_amount_cents - i.discount_amount_cents)/100.0 AS gross_after_discount,
       ROUND(SUM(i.refund_amount_cents)*1.0 / NULLIF(SUM(i.gross_amount_cents - i.discount_amount_cents),0), 6) AS refund_rate,
       (SUM(i.net_cents)/100.0)/COUNT(DISTINCT i.order_id) AS aov,
       t.visits
FROM items i
LEFT JOIN (SELECT period, channel, SUM(visitors) AS visits FROM traffic GROUP BY period, channel) t
  ON t.period = i.period AND t.channel = i.channel
WHERE i.period IN ('control','analysis')
GROUP BY i.period, i.channel, t.visits
ORDER BY i.channel, i.period