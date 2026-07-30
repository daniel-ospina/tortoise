import { Component } from 'react';

export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{
          display:'flex',alignItems:'center',justifyContent:'center',
          height:'100%',color:'#c0caf5',background:'#0a0e14',
          fontFamily:'-apple-system,sans-serif',flexDirection:'column',gap:12
        }}>
          <div style={{fontSize:48,opacity:0.4}}>⚠</div>
          <div style={{fontSize:16,color:'#f7768e'}}>{this.props.name||'View'} crashed</div>
          <div style={{fontSize:12,color:'#565f89',maxWidth:400,textAlign:'center'}}>
            {this.state.error?.message||'Unknown error'}
          </div>
          <button onClick={()=>this.setState({hasError:false,error:null})}
            style={{background:'#1a1f2e',border:'1px solid #1a2030',color:'#c0caf5',padding:'6px 16px',borderRadius:6,cursor:'pointer',fontSize:13,marginTop:8}}>
            Retry
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
