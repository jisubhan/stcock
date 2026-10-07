import RadarApp from '@/components/RadarApp';
export default async function StockPage({ params }: { params: Promise<{symbol:string}> }) { const {symbol}=await params; return <RadarApp initialSymbol={symbol}/>; }
