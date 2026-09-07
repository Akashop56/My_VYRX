package com.ronin.ai.ui
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import com.ronin.ai.network.UpdateProposal
@Composable fun ApprovalDialog(proposal:UpdateProposal,onApprove:()->Unit,onReject:()->Unit){ AlertDialog(onDismissRequest=onReject,title={Text("Core update approval")},text={Text("File: ${proposal.file_path}\n\n${proposal.summary}\n\nProposed code:\n${proposal.new_code.take(1000)}")},confirmButton={Button(onClick=onApprove){Text("APPROVE")}},dismissButton={OutlinedButton(onClick=onReject){Text("REJECT")}}) }
