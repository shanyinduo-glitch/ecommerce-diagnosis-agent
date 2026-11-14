SELECT period,
       category,
       SUM(net_cents)/100.0 AS net_sales,
       COUNT(DISTINCT order_id) AS order_count,
       SUM(pre_refund_cents)/100.0 AS pre_refund_sales,
       SUM(refund_amount_cents)/100.0 AS refund_amount,
       (SUM(net_cents)/100.0)/COUNT(DISTINCT order_id) AS aov,
       SUM(quantity) AS quantity
FROM items
WHERE period IN ('control','analysis') AND category = '服饰'
GROUP BY period, category
ORDER BY period